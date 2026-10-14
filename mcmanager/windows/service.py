"""Window-management facade used by the runtime layer.

Keeping this behind a small interface lets the mock environment (and, later,
other platforms) supply their own implementation without touching the UI or
the process tracker.
"""

from __future__ import annotations

from mcmanager.windows.discovery import WindowInfo, find_windows
from mcmanager.windows.focus import ActionResult, bring_to_front, close_window_gracefully


class WindowService:
    is_mock = False

    def find_windows(self, pids: set[int]) -> dict[int, WindowInfo]:
        return find_windows(pids)

    def bring_to_front(self, pid: int) -> ActionResult:
        return bring_to_front(pid)

    def close_gracefully(self, pid: int) -> ActionResult:
        return close_window_gracefully(pid)
