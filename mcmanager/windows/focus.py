"""Bringing a game window to the foreground, and closing it gracefully.

Activation uses ``NSRunningApplication`` (no permission needed). If the app
has Accessibility permission we additionally un-minimise and raise the exact
window, and can press its close button so Minecraft shuts down normally.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

from mcmanager.windows.permissions import accessibility_trusted

log = logging.getLogger(__name__)


@dataclass
class ActionResult:
    ok: bool
    message: str = ""


def _ax_windows(pid: int):
    import ApplicationServices as AS

    app = AS.AXUIElementCreateApplication(pid)
    AS.AXUIElementSetMessagingTimeout(app, 1.5)
    err, windows = AS.AXUIElementCopyAttributeValue(app, AS.kAXWindowsAttribute, None)
    if err != 0 or not windows:
        return app, []
    return app, list(windows)


def _ax_size(win) -> float:
    import ApplicationServices as AS

    err, value = AS.AXUIElementCopyAttributeValue(win, AS.kAXSizeAttribute, None)
    if err != 0 or value is None:
        return 0.0
    ok, size = AS.AXValueGetValue(value, AS.kAXValueCGSizeType, None)
    return size.width * size.height if ok else 0.0


def _main_ax_window(pid: int):
    _, windows = _ax_windows(pid)
    return max(windows, key=_ax_size) if windows else None


def bring_to_front(pid: int) -> ActionResult:
    if sys.platform != "darwin":
        return ActionResult(False, "Bringing windows forward isn't implemented on this platform.")
    import AppKit

    app = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    if app is None:
        return ActionResult(False, "The game process is no longer running.")

    note = ""
    if accessibility_trusted():
        try:
            import ApplicationServices as AS

            win = _main_ax_window(pid)
            if win is not None:
                AS.AXUIElementSetAttributeValue(win, AS.kAXMinimizedAttribute, False)
                AS.AXUIElementPerformAction(win, AS.kAXRaiseAction)
        except Exception:
            log.exception("Accessibility raise failed for pid %s", pid)
    else:
        note = "Grant Accessibility permission to also restore minimized windows."

    # macOS 14+ uses cooperative activation: the active app yields first.
    me = AppKit.NSApplication.sharedApplication()
    if hasattr(me, "yieldActivationToApplication_"):
        me.yieldActivationToApplication_(app)
    ok = app.activateWithOptions_(AppKit.NSApplicationActivateAllWindows)
    if not ok:
        return ActionResult(False, "macOS refused to activate the game window.")
    return ActionResult(True, note)


def close_window_gracefully(pid: int) -> ActionResult:
    """Press the game window's close button (requires Accessibility)."""
    if sys.platform != "darwin" or not accessibility_trusted():
        return ActionResult(False, "Accessibility permission not granted")
    try:
        import ApplicationServices as AS

        win = _main_ax_window(pid)
        if win is None:
            return ActionResult(False, "No accessible window")
        err, button = AS.AXUIElementCopyAttributeValue(win, AS.kAXCloseButtonAttribute, None)
        if err != 0 or button is None:
            return ActionResult(False, "Window has no close button")
        err = AS.AXUIElementPerformAction(button, AS.kAXPressAction)
        return ActionResult(err == 0, "" if err == 0 else f"AX error {err}")
    except Exception as exc:
        log.exception("Graceful close failed for pid %s", pid)
        return ActionResult(False, str(exc))
