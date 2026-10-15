"""Window-management facade used by the runtime layer.

The runtime only talks to a window service (find the game's window, bring it to the front,
close it gracefully), so each platform supplies its own implementation without touching the UI
or the process tracker:

* ``WindowService`` – macOS (Quartz window list, NSRunningApplication, Accessibility)
* ``Win32WindowService`` – Windows (Win32 API, see ``mcmanager.windows.win32``)
* ``MockWindowService`` – ``--mock`` mode (``mcmanager.mock.environment``)
"""

from __future__ import annotations

import sys

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


class Win32WindowService:
    is_mock = False

    def find_windows(self, pids: set[int]) -> dict[int, WindowInfo]:
        from mcmanager.windows import win32

        return win32.find_windows(pids)

    def bring_to_front(self, pid: int) -> ActionResult:
        from mcmanager.windows import win32

        return win32.bring_to_front(pid)

    def close_gracefully(self, pid: int) -> ActionResult:
        from mcmanager.windows import win32

        return win32.close_window_gracefully(pid)


def create_window_service():
    """The window service for the running platform."""
    return Win32WindowService() if sys.platform == "win32" else WindowService()
