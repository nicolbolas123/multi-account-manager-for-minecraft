"""The instance manager: dashboard entries + runtime state machine.

Concepts kept separate (see README):
    PrismInstance   - Prism's instance configuration (read-only to us)
    DashboardEntry  - the user's tile: display name, account profile, colour...
    RuntimeInfo     - the live process/window/log state for an entry

State transitions (per entry)::

    STOPPED/EXITED/CRASHED/FAILED --launch--> LAUNCHING --process found--> RUNNING
    RUNNING --stop--> STOPPING --process gone--> STOPPED
    RUNNING --process gone, not requested--> EXITED or CRASHED
    LAUNCHING --timeout / Prism error--> FAILED

A process that is already running when the app starts is *attached* to the
entry that owns its instance.
"""

from __future__ import annotations

import logging
import signal
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from mcmanager.config import AppSettings, DashboardEntry, GroupDef, SettingsStore
from mcmanager.config.settings import LABEL_COLORS
from mcmanager.prism import (
    LaunchError,
    LaunchRequest,
    PrismAccount,
    PrismInstallation,
    PrismInstance,
    build_launch_command,
    build_show_command,
    list_accounts,
    list_instances,
    spawn_prism,
)
from mcmanager.runtime.launch_queue import ItemState, LaunchBatch, LaunchQueue
from mcmanager.runtime.models import AppEvent, LogState, RunState, RuntimeInfo
from mcmanager.runtime.playtime import PlaytimeTracker
from mcmanager.runtime.poller import Poller, PollResult, game_dir_for
from mcmanager.runtime.process_finder import GameProcess, send_signal

log = logging.getLogger(__name__)

STOP_GRACE_S = 20.0


class PrismLaunchBackend:
    """Launches via the real Prism Launcher CLI."""

    is_mock = False

    def launch(self, install: PrismInstallation, request: LaunchRequest) -> subprocess.Popen:
        return spawn_prism(build_launch_command(install, request))

    def show(self, install: PrismInstallation, instance_id: str) -> None:
        spawn_prism(build_show_command(install, instance_id))


class InstanceManager(QObject):
    entries_changed = Signal()  # entries added/removed/reordered
    entry_updated = Signal(str)  # entry config or runtime changed
    notice = Signal(str, str)  # level ("info"|"warning"|"error"), message
    prism_changed = Signal()
    app_event = Signal(object)  # AppEvent, for notifications
    _poll_ready = Signal(object)

    def __init__(self, settings: AppSettings, store: SettingsStore, install: PrismInstallation,
                 launch_backend, window_service, locate=None, poll_interval: float = 1.0,
                 playtime: PlaytimeTracker | None = None):
        super().__init__()
        self.settings = settings
        self.store = store
        self.install = install
        self.launch_backend = launch_backend
        self.window_service = window_service
        self._locate = locate
        self.instances: dict[str, PrismInstance] = {}
        self.accounts: list[PrismAccount] = []
        self.runtime: dict[str, RuntimeInfo] = {e.id: RuntimeInfo() for e in settings.entries}
        self._game_procs: dict[str, GameProcess] = {}
        self._popens: dict[str, subprocess.Popen] = {}
        self._signatures: dict[str, tuple] = {}
        self._focus_hint_shown = False
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="actions")
        self._poll_ready.connect(self._on_poll)
        self.poller = Poller(window_service, self._poll_ready.emit, interval=poll_interval)
        # Play-time history lives next to the settings file (separate: it changes far more often).
        self.playtime = playtime or PlaytimeTracker(store.path.with_name("playtime.json"))
        self.queue = LaunchQueue(self)
        self.queue.batch_finished.connect(self._on_batch_finished)
        self._sync_groups()
        self.refresh_prism()

    # ----------------------------------------------------------------- lifecycle
    def start(self) -> None:
        self.poller.start()

    def shutdown(self) -> None:
        self.poller.stop()
        self.playtime.flush(force=True)  # games keep running; their sessions continue next time
        self._executor.shutdown(wait=False, cancel_futures=True)

    # --------------------------------------------------------------- prism data
    def refresh_prism(self, relocate: bool = False) -> None:
        if relocate and self._locate:
            self.install = self._locate(self.settings.prism_app_path, self.settings.prism_data_dir)
        self.instances = {i.id: i for i in list_instances(self.install.instances_dir)}
        self.accounts = list_accounts(self.install.data_dir)
        self._update_watch_dirs()
        self.prism_changed.emit()
        for entry in self.settings.entries:
            self.entry_updated.emit(entry.id)

    def instance_for(self, entry: DashboardEntry) -> PrismInstance | None:
        return self.instances.get(entry.prism_instance_id)

    def _update_watch_dirs(self) -> None:
        dirs = set()
        for entry in self.settings.entries:
            inst = self.instance_for(entry)
            if inst:
                dirs.add(str(inst.path))
        self.poller.set_instance_dirs(dirs)

    # ------------------------------------------------------------ entries CRUD
    @property
    def entries(self) -> list[DashboardEntry]:
        return self.settings.entries

    def entry(self, entry_id: str) -> DashboardEntry | None:
        return self.settings.entry(entry_id)

    def info(self, entry_id: str) -> RuntimeInfo:
        return self.runtime.setdefault(entry_id, RuntimeInfo())

    def save(self) -> None:
        try:
            self.store.save(self.settings)
        except OSError as exc:
            log.exception("Saving settings failed")
            self.notice.emit("error", f"Could not save settings: {exc}")

    def next_color(self) -> str:
        return LABEL_COLORS[len(self.settings.entries) % len(LABEL_COLORS)]

    def add_entry(self, entry: DashboardEntry) -> None:
        self.settings.entries.append(entry)
        self.runtime[entry.id] = RuntimeInfo()
        self._sync_groups()
        self.save()
        self._update_watch_dirs()
        self.entries_changed.emit()
        self.poller.poll_now()

    def update_entry(self, entry: DashboardEntry) -> None:
        if self._sync_groups():
            self.entries_changed.emit()
        self.save()
        self._update_watch_dirs()
        self.entry_updated.emit(entry.id)
        self.poller.poll_now()

    def remove_entry(self, entry_id: str) -> None:
        self.queue.cancel(entry_id, "Removed from the dashboard")
        self.settings.entries = [e for e in self.settings.entries if e.id != entry_id]
        self.runtime.pop(entry_id, None)
        self._game_procs.pop(entry_id, None)
        self.save()
        self._update_watch_dirs()
        self.entries_changed.emit()

    # ------------------------------------------------------------------ groups
    @property
    def groups(self) -> list[GroupDef]:
        return self.settings.groups

    def group(self, name: str) -> GroupDef | None:
        return next((g for g in self.settings.groups if g.name == name), None)

    def group_entries(self, name: str) -> list[DashboardEntry]:
        """Entries in a group ("" = ungrouped), in display order."""
        return [e for e in self.entries if e.group == name]

    def display_entries(self, name: str) -> list[DashboardEntry]:
        """Entries of a group as shown on the dashboard: the user's order, favorites first if enabled."""
        entries = self.group_entries(name)
        if self.settings.favorites_first:
            entries.sort(key=lambda e: not e.favorite)  # stable: keeps the user's order otherwise
        return entries

    def _sync_groups(self) -> bool:
        """Make sure every group an entry names exists (e.g. typed in the edit dialog)."""
        changed = False
        for entry in self.entries:
            entry.group = entry.group.strip()
            if entry.group and self.group(entry.group) is None:
                self.settings.groups.insert(self._pinned_count(), GroupDef(entry.group))
                changed = True
        # Keep pinned groups first (e.g. after a hand-edited settings file); sort is stable.
        ordered = sorted(self.settings.groups, key=lambda g: not g.pinned)
        if ordered != self.settings.groups:
            self.settings.groups[:] = ordered
            changed = True
        return changed

    def unique_group_name(self, base: str) -> str:
        base = base.strip() or "New Group"
        name, n = base, 2
        while self.group(name) is not None:
            name, n = f"{base} {n}", n + 1
        return name

    def add_group(self, name: str) -> str:
        """Create a group and return its (possibly de-duplicated) name."""
        name = self.unique_group_name(name)
        # New groups go to the top, just below any pinned groups.
        self.settings.groups.insert(self._pinned_count(), GroupDef(name))
        self.save()
        self.entries_changed.emit()
        return name

    def rename_group(self, old: str, new: str) -> bool:
        group, new = self.group(old), new.strip()
        if group is None or not new or (new != old and self.group(new) is not None):
            return False
        group.name = new
        for entry in self.group_entries(old):
            entry.group = new
        self.save()
        self.entries_changed.emit()
        return True

    def remove_group(self, name: str) -> None:
        """Delete a group; its entries move to the ungrouped section (nothing else is removed)."""
        self.settings.groups = [g for g in self.settings.groups if g.name != name]
        for entry in self.group_entries(name):
            entry.group = ""
        self.save()
        self.entries_changed.emit()

    def set_group_collapsed(self, name: str, collapsed: bool) -> None:
        group = self.group(name)
        if group is not None and group.collapsed != collapsed:
            group.collapsed = collapsed
            self.save()
            self.entries_changed.emit()

    # Group order: settings.groups is kept as [pinned..., unpinned...]; that list order is display order.
    def _group_index(self, name: str) -> int | None:
        return next((i for i, g in enumerate(self.settings.groups) if g.name == name), None)

    def _pinned_count(self) -> int:
        return sum(1 for g in self.settings.groups if g.pinned)

    def _block(self, group: GroupDef) -> range:
        """Index range of the block (pinned or unpinned) that ``group`` can move within."""
        pinned = self._pinned_count()
        return range(0, pinned) if group.pinned else range(pinned, len(self.settings.groups))

    def can_move_group(self, name: str, offset: int) -> bool:
        i = self._group_index(name)
        return i is not None and (i + offset) in self._block(self.settings.groups[i])

    def move_group(self, name: str, offset: int) -> None:
        """Move a group up (negative) or down (positive), never across the pinned/unpinned boundary."""
        groups = self.settings.groups
        i = self._group_index(name)
        if i is None:
            return
        block = self._block(groups[i])
        target = min(max(i + offset, block.start), block.stop - 1)
        if target == i:
            return
        groups.insert(target, groups.pop(i))
        self.save()
        self.entries_changed.emit()

    def set_group_pinned(self, name: str, pinned: bool) -> None:
        """Pin (keep at the top) or unpin a group. Either way it lands at the pinned/unpinned boundary."""
        group = self.group(name)
        if group is None or group.pinned == pinned:
            return
        self.settings.groups.remove(group)
        group.pinned = pinned
        self.settings.groups.insert(self._pinned_count(), group)
        self.save()
        self.entries_changed.emit()

    def move_entries(self, entry_ids: list[str], group: str, before_id: str | None = None) -> None:
        """Move several entries (kept in the given order) into ``group`` before ``before_id``."""
        moving = [e for e in entry_ids if self.entry(e)]
        if len(moving) <= 1:
            if moving:
                self.move_entry(moving[0], group, before_id)
            return
        if group and self.group(group) is None:
            return
        if before_id in moving:  # dropped onto one of the moved cards: anchor on the next card that stays
            order = [e.id for e in self.entries]
            after = order[order.index(before_id):]
            before_id = next((i for i in after if i not in moving and self.entry(i).group == group), None)
        entries = self.settings.entries
        moved = [self.entry(e) for e in moving]
        for entry in moved:
            entries.remove(entry)
            entry.group = group
        index = next((i for i, e in enumerate(entries) if e.id == before_id), None)
        if index is None:
            members = [i for i, e in enumerate(entries) if e.group == group]
            index = members[-1] + 1 if members else len(entries)
        entries[index:index] = moved
        self.save()
        self.entries_changed.emit()

    def move_entry(self, entry_id: str, group: str, before_id: str | None = None) -> None:
        """Put an entry into ``group`` ("" = ungrouped), placed before ``before_id`` or at the group's end."""
        entry = self.entry(entry_id)
        if entry is None or (group and self.group(group) is None):
            return
        entries = self.settings.entries
        if before_id == entry_id:  # dropped onto its own spot
            if entry.group == group:
                return
            before_id = None
        entries.remove(entry)
        entry.group = group
        index = next((i for i, e in enumerate(entries) if e.id == before_id), None)
        if index is None:
            members = [i for i, e in enumerate(entries) if e.group == group]
            index = members[-1] + 1 if members else len(entries)
        entries.insert(index, entry)
        self.save()
        self.entries_changed.emit()

    def entries_sharing_instance(self, entry: DashboardEntry) -> list[DashboardEntry]:
        return [e for e in self.entries if e.id != entry.id and e.prism_instance_id == entry.prism_instance_id]

    def unused_instances(self) -> list[PrismInstance]:
        used = {e.prism_instance_id for e in self.entries}
        return [i for i in self.instances.values() if i.id not in used]

    def import_all_instances(self) -> int:
        added = 0
        for inst in self.unused_instances():
            self.settings.entries.append(
                DashboardEntry(display_name=inst.name, prism_instance_id=inst.id, color=self.next_color())
            )
            self.runtime[self.settings.entries[-1].id] = RuntimeInfo()
            added += 1
        if added:
            self.save()
            self._update_watch_dirs()
            self.entries_changed.emit()
            self.poller.poll_now()
        return added

    # ------------------------------------------------------------------ actions
    def running_count(self) -> int:
        return sum(1 for e in self.entries if self.info(e.id).state.is_active)

    def can_launch(self, entry_id: str) -> tuple[bool, str]:
        entry = self.entry(entry_id)
        if entry is None:
            return False, "Unknown entry"
        if self.instance_for(entry) is None:
            return False, f"Prism instance '{entry.prism_instance_id}' was not found."
        if self.info(entry_id).state.is_active:
            return False, f"'{entry.display_name}' is already running."
        for other in self.entries_sharing_instance(entry):
            if self.info(other.id).state.is_active:
                return False, (
                    f"Prism instance '{entry.prism_instance_id}' is already in use by '{other.display_name}'. "
                    "Prism runs each instance once; copy the instance in Prism to run another account."
                )
        if not self.launch_backend.is_mock and not self.install.can_launch:
            return False, "Prism Launcher was not found. Set its location in Settings."
        return True, ""

    def launch(self, entry_id: str, from_queue: bool = False) -> bool:
        if not from_queue:
            self.queue.cancel(entry_id, "Launched directly")  # a direct launch overtakes the queue
        ok, reason = self.can_launch(entry_id)
        if not ok:
            if not from_queue:
                self.notice.emit("warning", reason)
            return False
        entry = self.entry(entry_id)
        request = LaunchRequest(instance_id=entry.prism_instance_id, profile=entry.account_profile)
        self.playtime.note_launch_requested(entry_id)
        try:
            popen = self.launch_backend.launch(self.install, request)
        except LaunchError as exc:
            self._set(entry_id, state=RunState.FAILED, message=str(exc))
            self.notice.emit("error", str(exc))
            self.report_launch_failure(entry_id, str(exc))
            return False
        self._popens[entry_id] = popen
        info = self.info(entry_id)
        info.state = RunState.LAUNCHING
        info.launch_requested_at = time.time()
        info.stop_requested_at = None
        info.message = "Starting through Prism Launcher…"
        info.crash_report = None
        info.log = type(info.log)()
        self.entry_updated.emit(entry_id)
        self.poller.poll_now()
        log.info("Launch requested for %s (instance %s)", entry.display_name, entry.prism_instance_id)
        return True

    def launch_many(self, entry_ids: list[str]) -> int:
        """Launch several accounts through the launch queue (staggered if enabled). Returns how many were queued."""
        startable = [e for e in entry_ids if self.can_launch(e)[0] and not self.queue.is_waiting(e)]
        if not startable:
            self.notice.emit("info", "Nothing to launch: every instance is already running, queued or unavailable.")
            return 0
        return self.queue.enqueue(startable, staggered=self.settings.staggered_launch)

    def report_launch_failure(self, entry_id: str, reason: str, shown: bool = True) -> None:
        entry = self.entry(entry_id)
        if entry is None:
            return
        info = self.info(entry_id)
        key = f"launch_failed:{entry_id}:{info.launch_requested_at or time.time()}"
        self.app_event.emit(AppEvent("launch_failed", f"{entry.display_name} failed to launch", reason, key, entry_id,
                                     shown_in_app=shown))

    def _on_batch_finished(self, batch: LaunchBatch) -> None:
        if not batch.is_bulk:
            return
        started, failed = batch.count(ItemState.STARTED), batch.count(ItemState.FAILED)
        skipped, cancelled = batch.count(ItemState.SKIPPED), batch.count(ItemState.CANCELLED)
        parts = [f"{started} started"]
        parts += [f"{n} {label}" for n, label in ((failed, "failed"), (skipped, "skipped"), (cancelled, "cancelled")) if n]
        summary = ", ".join(parts) + "."
        if failed:
            names = [self.entry(i.entry_id).display_name for i in batch.items
                     if i.state == ItemState.FAILED and self.entry(i.entry_id)]
            summary += " Failed: " + ", ".join(names) + "."
        key = f"bulk:{batch.batch_id}"
        if failed:  # one notification; it also satisfies users who enabled only "Bulk launch completed"
            self.app_event.emit(AppEvent("bulk_failed", "Bulk launch finished with errors", summary, key,
                                         also=("bulk_completed",), shown_in_app=True))
        else:
            self.app_event.emit(AppEvent("bulk_completed", "Bulk launch completed", summary, key, shown_in_app=True))
        self.notice.emit("warning" if failed else "info", "Bulk launch: " + summary)

    def stop_many(self, entry_ids: list[str]) -> int:
        targets = [e for e in entry_ids if self.info(e).state == RunState.RUNNING]
        for entry_id in targets:
            self.stop(entry_id)
        return len(targets)

    def restart_many(self, entry_ids: list[str]) -> int:
        targets = [e for e in entry_ids if self.info(e).state == RunState.RUNNING]
        for entry_id in targets:
            self.restart(entry_id)
        return len(targets)

    def stop(self, entry_id: str) -> None:
        info = self.info(entry_id)
        proc = self._game_procs.get(entry_id)
        if info.state == RunState.LAUNCHING and proc is None:
            self.notice.emit("info", "The game has not started yet. Cancel the launch from Prism Launcher if needed.")
            return
        if info.state not in (RunState.RUNNING, RunState.STOPPING) or proc is None:
            return
        info.state = RunState.STOPPING
        info.stop_requested_at = time.time()
        info.message = "Closing the game…"
        self.entry_updated.emit(entry_id)
        self._executor.submit(self._stop_worker, proc)

    def _stop_worker(self, proc: GameProcess) -> None:
        result = self.window_service.close_gracefully(proc.pid)
        if result.ok:
            log.info("Requested graceful close of pid %s via its window", proc.pid)
        else:
            log.info("Graceful close unavailable (%s); sending SIGTERM to pid %s", result.message, proc.pid)
            send_signal(proc, signal.SIGTERM)
        self.poller.poll_now()

    def force_quit(self, entry_id: str) -> None:
        proc = self._game_procs.get(entry_id)
        if proc is None:
            return
        info = self.info(entry_id)
        info.state = RunState.STOPPING
        info.stop_requested_at = info.stop_requested_at or time.time()
        if send_signal(proc, signal.SIGKILL):
            info.message = "Force quit"
        self.entry_updated.emit(entry_id)
        self.poller.poll_now()

    def restart(self, entry_id: str) -> None:
        info = self.info(entry_id)
        if info.state == RunState.RUNNING:
            info.restart_pending = True
            self.stop(entry_id)
        elif not info.state.is_active:
            self.launch(entry_id)

    def stop_all(self) -> None:
        for entry in self.entries:
            if self.info(entry.id).state == RunState.RUNNING:
                self.stop(entry.id)

    def bring_to_front(self, entry_id: str) -> None:
        info = self.info(entry_id)
        if info.pid is None:
            self.notice.emit("info", "That instance is not running.")
            return
        result = self.window_service.bring_to_front(info.pid)
        if not result.ok:
            self.notice.emit("warning", result.message)
        elif result.message and not self._focus_hint_shown:
            self._focus_hint_shown = True  # informational hint; show once per session
            self.notice.emit("info", result.message)

    def show_in_prism(self, entry_id: str) -> None:
        entry = self.entry(entry_id)
        try:
            self.launch_backend.show(self.install, entry.prism_instance_id)
        except LaunchError as exc:
            self.notice.emit("error", str(exc))

    def game_dir(self, entry_id: str) -> Path | None:
        entry = self.entry(entry_id)
        inst = self.instance_for(entry) if entry else None
        return game_dir_for(inst.path) if inst else None

    # ------------------------------------------------------------- state machine
    def _set(self, entry_id: str, **changes) -> None:
        info = self.info(entry_id)
        for k, v in changes.items():
            setattr(info, k, v)
        self.entry_updated.emit(entry_id)

    def _find_crash_report(self, entry_id: str, since: float | None) -> str | None:
        game = self.game_dir(entry_id)
        if game is None or since is None:
            return None
        reports = [p for p in (game / "crash-reports").glob("crash-*.txt") if p.stat().st_mtime >= since - 1]
        return str(max(reports, key=lambda p: p.stat().st_mtime)) if reports else None

    def _assign_processes(self, result: PollResult) -> dict[str, GameProcess]:
        """Give each found process to exactly one entry.

        Priority: the entry already tracking it, then an entry that is launching
        that instance, then the first entry using the instance.
        """
        def priority(entry: DashboardEntry) -> int:
            inst = self.instance_for(entry)
            proc = result.processes.get(str(inst.path)) if inst else None
            if entry.id in self._game_procs or (proc and self.playtime.owns(entry.id, proc.pid, proc.create_time)):
                return 0  # already tracking it (now, or in a play-time session saved before a restart)
            return 1 if self.info(entry.id).state == RunState.LAUNCHING else 2

        assigned: dict[str, GameProcess] = {}
        taken: set[int] = set()
        for entry in sorted(self.entries, key=priority):
            inst = self.instance_for(entry)
            proc = result.processes.get(str(inst.path)) if inst else None
            if proc and proc.pid not in taken:
                assigned[entry.id] = proc
                taken.add(proc.pid)
        return assigned

    def _on_poll(self, result: PollResult) -> None:
        now = time.time()
        assigned = self._assign_processes(result)
        for entry in self.entries:
            inst = self.instance_for(entry)
            key = str(inst.path) if inst else None
            info = self.info(entry.id)
            proc = assigned.get(entry.id)
            if key in result.logs and (proc or entry.id in self._game_procs):
                info.log = result.logs[key]

            popen = self._popens.get(entry.id)
            if popen is not None:
                rc = popen.poll()
                if proc is not None:
                    self._popens.pop(entry.id, None)
                elif rc is not None:
                    self._popens.pop(entry.id, None)
                    if rc != 0 and info.state == RunState.LAUNCHING:
                        info.state = RunState.FAILED
                        info.message = f"Prism Launcher exited with code {rc}. See Logs/prism-cli.log."
                        self.notice.emit("error", f"{entry.display_name}: {info.message}")
                        self.report_launch_failure(entry.id, info.message)

            if proc is not None:
                self._on_process_present(entry, info, proc, result, now)
                self.playtime.observe(entry.id, proc.pid, proc.create_time, entry.prism_instance_id)
            else:
                self._on_process_absent(entry, info, now)
                ended = {RunState.STOPPED: "stopped", RunState.CRASHED: "crashed"}.get(info.state, "exited")
                self.playtime.observe(entry.id, None, None, ended=ended)
            self._emit_if_changed(entry.id, info)
        self.playtime.flush()

    def _on_process_present(self, entry, info: RuntimeInfo, proc: GameProcess, result: PollResult, now: float):
        if info.pid != proc.pid:
            if info.state == RunState.LAUNCHING:
                self.app_event.emit(AppEvent("started", f"{entry.display_name} started",
                                             "The game is running.", f"started:{entry.id}:{proc.pid}", entry.id))
            info.attached = info.state != RunState.LAUNCHING
            info.state = RunState.RUNNING
            info.pid = proc.pid
            info.process_started_at = proc.create_time
            info.stop_requested_at = None
            info.message = "Attached to a game that was already running" if info.attached else None
            info.crash_report = None
            self._game_procs[entry.id] = proc
            log.info("%s: game process %s %s", entry.display_name, proc.pid, "attached" if info.attached else "started")
        elif info.state not in (RunState.RUNNING, RunState.STOPPING):
            info.state = RunState.RUNNING
        info.window = result.windows.get(proc.pid)
        usage = result.usage.get(proc.pid)
        info.cpu_percent, info.memory_bytes = usage if usage else (None, None)
        if info.state == RunState.RUNNING and info.window is None and not info.attached and info.message is None:
            info.message = "Waiting for the game window…"
        elif info.state == RunState.RUNNING and info.window is not None and info.message == "Waiting for the game window…":
            info.message = None
        if info.state == RunState.STOPPING and info.stop_requested_at and now - info.stop_requested_at > STOP_GRACE_S:
            info.message = "The game is not closing. Use Force Quit if it is frozen."

    def _on_process_absent(self, entry, info: RuntimeInfo, now: float):
        if info.state in (RunState.RUNNING, RunState.STOPPING):
            requested = info.stop_requested_at is not None
            crash = None if requested else self._find_crash_report(entry.id, info.process_started_at)
            if requested:
                info.state, info.message = RunState.STOPPED, None
            elif crash:
                info.state, info.message, info.crash_report = RunState.CRASHED, "The game crashed.", crash
                self.notice.emit("error", f"{entry.display_name} crashed. Crash report: {crash}")
                self.app_event.emit(AppEvent("crashed", f"{entry.display_name} crashed",
                                             "Minecraft crashed and wrote a crash report.",
                                             f"exit:{entry.id}:{info.pid}", entry.id, shown_in_app=True))
            elif info.log.clean_shutdown:
                info.state, info.message = RunState.EXITED, "Closed from inside the game."
            else:
                info.state, info.message = RunState.EXITED, "The game process ended unexpectedly."
                self.notice.emit("warning", f"{entry.display_name}: the game process ended unexpectedly.")
                self.app_event.emit(AppEvent("crashed", f"{entry.display_name} closed unexpectedly",
                                             "The game process ended without a crash report or a normal quit.",
                                             f"exit:{entry.id}:{info.pid}", entry.id, shown_in_app=True))
            log.info("%s: process %s ended -> %s", entry.display_name, info.pid, info.state.value)
            info.pid = info.window = info.cpu_percent = info.memory_bytes = None
            info.log = LogState(username=info.log.username)  # connection no longer applies
            info.stop_requested_at = None
            self._game_procs.pop(entry.id, None)
            if info.restart_pending:
                info.restart_pending = False
                if self.settings.staggered_launch:
                    self.queue.enqueue([entry.id], staggered=True)  # several restarts start one by one
                else:
                    QTimer.singleShot(1500, lambda eid=entry.id: self.launch(eid))
        elif info.state == RunState.LAUNCHING and info.launch_requested_at:
            timeout = self.settings.launch_timeout_s
            if now - info.launch_requested_at > timeout:
                info.state = RunState.FAILED
                info.message = (
                    f"Minecraft did not start within {timeout} s. Check Prism Launcher for "
                    "sign-in, download or Java errors."
                )
                self._popens.pop(entry.id, None)
                self.notice.emit("error", f"{entry.display_name}: {info.message}")
                self.report_launch_failure(entry.id, info.message)

    def _emit_if_changed(self, entry_id: str, info: RuntimeInfo) -> None:
        sig = (
            info.state, info.pid, info.window, info.message, info.log.username, info.log.connection,
            round(info.cpu_percent or 0), (info.memory_bytes or 0) >> 20,
        )
        if self._signatures.get(entry_id) != sig:
            self._signatures[entry_id] = sig
            self.entry_updated.emit(entry_id)
