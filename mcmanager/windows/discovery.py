"""Discovering the game window that belongs to a process (macOS Quartz window list).

``WindowInfo`` and ``choose_window`` are shared with the Windows implementation
(``mcmanager.windows.win32``); ``window_id`` is a CGWindowID on macOS and an HWND on Windows.

Listing windows and their owner PIDs does not require Screen Recording
permission; only window *titles* and *contents* do.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

log = logging.getLogger(__name__)

MIN_GAME_WINDOW_SIZE = 120  # points; filters out menu bar helpers and tooltips


@dataclass(frozen=True)
class WindowInfo:
    window_id: int
    pid: int
    width: int
    height: int
    x: int = 0
    y: int = 0
    onscreen: bool = True
    title: str | None = None

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 16 / 9


def choose_window(candidates: list[WindowInfo]) -> WindowInfo | None:
    usable = [w for w in candidates if w.width >= MIN_GAME_WINDOW_SIZE and w.height >= MIN_GAME_WINDOW_SIZE]
    if not usable:
        return None
    # Prefer on-screen windows, then the largest (the game canvas).
    return max(usable, key=lambda w: (w.onscreen, w.width * w.height))


def find_windows(pids: set[int]) -> dict[int, WindowInfo]:
    """Return the most likely main window for each PID that has one."""
    if sys.platform != "darwin" or not pids:
        return {}
    import Quartz

    try:
        raw = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID) or []
    except Exception:  # pragma: no cover - defensive, Quartz errors are rare
        log.exception("CGWindowListCopyWindowInfo failed")
        return {}

    by_pid: dict[int, list[WindowInfo]] = {}
    for w in raw:
        pid = int(w.get("kCGWindowOwnerPID", -1))
        if pid not in pids or w.get("kCGWindowLayer", 0) != 0 or w.get("kCGWindowAlpha", 1.0) <= 0:
            continue
        b = w.get("kCGWindowBounds") or {}
        by_pid.setdefault(pid, []).append(
            WindowInfo(
                window_id=int(w["kCGWindowNumber"]),
                pid=pid,
                width=int(b.get("Width", 0)),
                height=int(b.get("Height", 0)),
                x=int(b.get("X", 0)),
                y=int(b.get("Y", 0)),
                onscreen=bool(w.get("kCGWindowIsOnscreen", False)),
                title=w.get("kCGWindowName"),
            )
        )
    result = {}
    for pid, windows in by_pid.items():
        chosen = choose_window(windows)
        if chosen:
            result[pid] = chosen
    return result
