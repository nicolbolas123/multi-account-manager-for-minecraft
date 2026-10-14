"""Background polling of processes, windows, logs and resource usage.

All potentially slow system queries happen on this thread; the UI thread only
receives immutable :class:`PollResult` snapshots.
"""

from __future__ import annotations

import copy
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import psutil

from mcmanager.runtime.log_watcher import LogTail, apply_line
from mcmanager.runtime.models import LogState
from mcmanager.runtime.process_finder import GameProcess, scan_game_processes
from mcmanager.windows.discovery import WindowInfo

log = logging.getLogger(__name__)


def game_dir_for(instance_dir: Path) -> Path:
    for sub in ("minecraft", ".minecraft"):
        if (instance_dir / sub).is_dir():
            return instance_dir / sub
    return instance_dir / "minecraft"


@dataclass
class PollResult:
    timestamp: float
    processes: dict[str, GameProcess | None] = field(default_factory=dict)
    duplicate_counts: dict[str, int] = field(default_factory=dict)
    windows: dict[int, WindowInfo] = field(default_factory=dict)
    logs: dict[str, LogState] = field(default_factory=dict)
    usage: dict[int, tuple[float, int]] = field(default_factory=dict)


@dataclass
class _LogTracker:
    pid: int
    tail: LogTail
    state: LogState


class Poller:
    def __init__(self, window_service, callback: Callable[[PollResult], None], interval: float = 1.0):
        self._window_service = window_service
        self._callback = callback
        self.interval = interval
        self._dirs: set[str] = set()
        self._logs: dict[str, _LogTracker] = {}
        self._procs: dict[int, psutil.Process] = {}
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None

    def set_instance_dirs(self, dirs: set[str]) -> None:
        with self._lock:
            self._dirs = set(dirs)
        self._wake.set()

    def poll_now(self) -> None:
        self._wake.set()

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run, name="poller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while self._running:
            try:
                result = self.poll_once()
                if self._running:
                    self._callback(result)
            except Exception:
                log.exception("Poll failed")
            self._wake.wait(self.interval)
            self._wake.clear()

    def poll_once(self) -> PollResult:
        with self._lock:
            dirs = set(self._dirs)
        result = PollResult(timestamp=time.time())
        found = scan_game_processes(Path(d) for d in dirs)
        for d, procs in found.items():
            result.processes[d] = procs[0] if procs else None
            result.duplicate_counts[d] = len(procs)

        pids = {p.pid for p in result.processes.values() if p}
        if pids:
            try:
                result.windows = self._window_service.find_windows(pids)
            except Exception:
                log.exception("Window discovery failed")
        result.usage = self._usage(pids)
        result.logs = self._read_logs(dirs, result.processes)
        return result

    def _usage(self, pids: set[int]) -> dict[int, tuple[float, int]]:
        for pid in list(self._procs):
            if pid not in pids:
                del self._procs[pid]
        usage = {}
        for pid in pids:
            try:
                proc = self._procs.get(pid)
                if proc is None:
                    proc = self._procs[pid] = psutil.Process(pid)
                    proc.cpu_percent(None)  # prime; first value is meaningless
                usage[pid] = (proc.cpu_percent(None), proc.memory_info().rss)
            except psutil.Error:
                self._procs.pop(pid, None)
        return usage

    def _read_logs(self, dirs: set[str], processes: dict[str, GameProcess | None]) -> dict[str, LogState]:
        states = {}
        for d in dirs | set(self._logs):
            proc = processes.get(d)
            tracker = self._logs.get(d)
            if proc and (tracker is None or tracker.pid != proc.pid):
                path = game_dir_for(Path(d)) / "logs" / "latest.log"
                tracker = self._logs[d] = _LogTracker(proc.pid, LogTail(path, not_before=proc.create_time), LogState())
            if tracker is None:
                continue
            lines, reset = tracker.tail.read_new_lines()
            if reset:
                tracker.state = LogState()
            for line in lines:
                apply_line(tracker.state, line)
            states[d] = copy.copy(tracker.state)
            if not proc:
                # Process gone: this was the final read (catches "Stopping!").
                del self._logs[d]
        return states
