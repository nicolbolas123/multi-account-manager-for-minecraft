"""Per-account play time, tracked from the game process lifetime.

Why not Prism's numbers? Prism stores ``totalTimePlayed`` / ``lastTimePlayed`` /
``lastLaunchTime`` per *instance* in ``instance.cfg``. Several dashboard
accounts can share an instance, there is no session history, and only launches
Prism supervised are counted. So sessions are tracked here instead (Prism's
total is shown in the expanded view for reference only).

Model
-----
A *session* is one game process: it starts at the process's creation time and
ends when the process disappears. Sessions are identified by ``(pid,
create_time)``, so seeing the same process again (every poll, after the
dashboard restarts, or when it is re-attached) continues the same session
instead of starting a new one.

While a session is open its ``last_seen`` time is saved periodically (the
heartbeat). If the dashboard itself was closed or crashed and the process is
gone when it comes back, the session is closed at ``last_seen`` and marked
``estimated`` — at most one heartbeat interval is lost, and nothing is
over-counted.

Data is keyed by the dashboard entry id (a stable UUID), never by display
name or position, so renaming, reordering or regrouping cannot corrupt it.
All durations are seconds.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from mcmanager.config.settings import atomic_write_json, read_json_or_backup

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
HEARTBEAT_S = 60.0  # how often an open session's last_seen is written to disk
MAX_HISTORY = 200  # sessions kept per account; totals are kept separately and never truncated
SAME_PROCESS_TOLERANCE_S = 2.0  # create_time jitter allowed when matching a known process


@dataclass
class Session:
    start: float
    end: float
    pid: int | None = None
    instance_id: str | None = None
    ended: str = "exited"  # exited | crashed | stopped | estimated
    estimated: bool = False  # end time inferred from the last heartbeat

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class OpenSession:
    pid: int
    start: float  # process create_time
    last_seen: float
    instance_id: str | None = None
    seen_this_run: bool = field(default=False, compare=False)  # not persisted

    def matches(self, pid: int, start: float) -> bool:
        return self.pid == pid and abs(self.start - start) <= SAME_PROCESS_TOLERANCE_S


@dataclass
class AccountPlaytime:
    total_s: float = 0.0  # closed sessions only
    sessions: list[Session] = field(default_factory=list)  # newest last
    open: OpenSession | None = None
    last_launch_requested: float | None = None


def format_duration(seconds: float | None) -> str:
    """12h 35m / 35m / 42s."""
    if seconds is None:
        return "—"
    s = int(max(0, seconds))
    if s < 60:
        return f"{s}s"
    h, m = divmod(s // 60, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


class PlaytimeTracker:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path = path
        self.clock = clock
        self.accounts: dict[str, AccountPlaytime] = {}
        self._dirty = False
        self._last_flush = 0.0
        self._load()

    # ------------------------------------------------------------- persistence
    def _load(self) -> None:
        data = read_json_or_backup(self.path)
        if not isinstance(data, dict):
            return
        for entry_id, raw in (data.get("accounts") or {}).items():
            try:
                acct = AccountPlaytime(
                    total_s=float(raw.get("total_s", 0.0)),
                    sessions=[Session(**_known(Session, s)) for s in raw.get("sessions", [])],
                    last_launch_requested=raw.get("last_launch_requested"),
                )
                if raw.get("open"):
                    acct.open = OpenSession(**_known(OpenSession, raw["open"]))
                    acct.open.seen_this_run = False
                if acct.total_s < 0:
                    acct.total_s = sum(s.duration for s in acct.sessions)
                self.accounts[str(entry_id)] = acct
            except (TypeError, ValueError, AttributeError) as exc:
                log.warning("Skipping malformed play-time record for %s: %s", entry_id, exc)

    def save(self) -> None:
        data = {"schema_version": SCHEMA_VERSION, "accounts": {}}
        for entry_id, acct in self.accounts.items():
            record = {
                "total_s": round(acct.total_s, 3),
                "sessions": [asdict(s) for s in acct.sessions],
                "last_launch_requested": acct.last_launch_requested,
            }
            if acct.open:
                record["open"] = {k: v for k, v in asdict(acct.open).items() if k != "seen_this_run"}
            data["accounts"][entry_id] = record
        try:
            atomic_write_json(self.path, data)
            self._dirty = False
            self._last_flush = self.clock()
        except OSError:
            log.exception("Could not save play time")

    def flush(self, force: bool = False) -> None:
        """Save if something changed, or on heartbeat while sessions are open."""
        now = self.clock()
        heartbeat_due = any(a.open for a in self.accounts.values()) and now - self._last_flush >= HEARTBEAT_S
        if force or self._dirty or heartbeat_due:
            self.save()

    # ---------------------------------------------------------------- tracking
    def _acct(self, entry_id: str) -> AccountPlaytime:
        return self.accounts.setdefault(entry_id, AccountPlaytime())

    def note_launch_requested(self, entry_id: str) -> None:
        self._acct(entry_id).last_launch_requested = self.clock()
        self._dirty = True

    def owns(self, entry_id: str, pid: int, start: float) -> bool:
        acct = self.accounts.get(entry_id)
        return bool(acct and acct.open and acct.open.matches(pid, start))

    def observe(self, entry_id: str, pid: int | None, start: float | None,
                instance_id: str | None = None, ended: str = "exited") -> None:
        """Report what process (if any) an account has right now. Call on every poll.

        ``ended`` describes why the previous process went away, if it did.
        """
        now = self.clock()
        acct = self._acct(entry_id)
        current = acct.open
        if pid is not None and start is not None:
            if current and current.matches(pid, start):
                current.last_seen = now
                current.seen_this_run = True
                return
            if current:  # a different process: the previous one ended while we were not looking
                self._close(acct, current.last_seen, "estimated", estimated=True)
            acct.open = OpenSession(pid=pid, start=min(start, now), last_seen=now,
                                    instance_id=instance_id, seen_this_run=True)
            self._dirty = True
            log.info("Play time: session started for %s (pid %s)", entry_id, pid)
        elif current:
            if current.seen_this_run:
                self._close(acct, now, ended)
            else:  # ended while the dashboard was closed: trust the last heartbeat only
                self._close(acct, current.last_seen, "estimated", estimated=True)

    def _close(self, acct: AccountPlaytime, end: float, ended: str, estimated: bool = False) -> None:
        current = acct.open
        acct.open = None
        session = Session(start=current.start, end=max(current.start, end), pid=current.pid,
                          instance_id=current.instance_id, ended=ended, estimated=estimated)
        acct.total_s += session.duration
        acct.sessions.append(session)
        del acct.sessions[:-MAX_HISTORY]
        self._dirty = True
        log.info("Play time: session ended (%s, %s)", ended, format_duration(session.duration))

    # ----------------------------------------------------------------- queries
    def current_session_s(self, entry_id: str) -> float | None:
        acct = self.accounts.get(entry_id)
        if not acct or not acct.open:
            return None
        return max(0.0, self.clock() - acct.open.start)

    def total_s(self, entry_id: str) -> float:
        acct = self.accounts.get(entry_id)
        if not acct:
            return 0.0
        return acct.total_s + (self.current_session_s(entry_id) or 0.0)

    def combined_s(self, entry_ids) -> float:
        return sum(self.total_s(e) for e in entry_ids)

    def last_session(self, entry_id: str) -> Session | None:
        acct = self.accounts.get(entry_id)
        return acct.sessions[-1] if acct and acct.sessions else None

    def last_launch_requested(self, entry_id: str) -> float | None:
        acct = self.accounts.get(entry_id)
        return acct.last_launch_requested if acct else None


def _known(cls, raw: dict) -> dict:
    names = cls.__dataclass_fields__.keys()
    return {k: v for k, v in raw.items() if k in names}
