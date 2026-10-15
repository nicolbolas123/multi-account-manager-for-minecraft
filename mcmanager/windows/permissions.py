"""macOS privacy permissions used by the app.

Windows has no equivalent: any process may capture and focus the windows of other processes of
the same user, so on other platforms the checks below report "granted" and the UI hides them
(see ``mcmanager.system.needs_privacy_permissions``).

* Screen Recording - required to capture window contents (live previews).
* Accessibility    - optional; lets us un-minimise/raise a specific window and
                     close the game window gracefully. Without it we fall back
                     to activating the app and SIGTERM.

When run from a terminal, macOS attributes these permissions to the terminal
application (Terminal, iTerm, ...), so that is the app to enable in
System Settings.
"""

from __future__ import annotations

from mcmanager import APP_NAME
from mcmanager.bundle import is_frozen


def permission_holder() -> str:
    """Which app macOS grants permissions to: the .app itself, or the terminal that started us."""
    return f"“{APP_NAME}”" if is_frozen() else "the terminal app that started it (Terminal, iTerm…)"

import logging
import sys

log = logging.getLogger(__name__)

SCREEN_RECORDING_SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
ACCESSIBILITY_SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
NOTIFICATIONS_SETTINGS_URL = ("x-apple.systempreferences:com.apple.Notifications-Settings.extension"
                              if sys.platform == "darwin" else "ms-settings:notifications")


def screen_capture_allowed() -> bool:
    if sys.platform != "darwin":
        return True  # no permission needed
    import Quartz

    try:
        return bool(Quartz.CGPreflightScreenCaptureAccess())
    except Exception:
        log.exception("CGPreflightScreenCaptureAccess failed")
        return False


def request_screen_capture() -> bool:
    """Ask macOS to show its Screen Recording prompt (only shown once per app)."""
    if sys.platform != "darwin":
        return False
    import Quartz

    return bool(Quartz.CGRequestScreenCaptureAccess())


def accessibility_trusted() -> bool:
    if sys.platform != "darwin":
        return True  # no permission needed
    import ApplicationServices as AS

    return bool(AS.AXIsProcessTrusted())


def request_accessibility() -> bool:
    if sys.platform != "darwin":
        return False
    import ApplicationServices as AS

    return bool(AS.AXIsProcessTrustedWithOptions({AS.kAXTrustedCheckOptionPrompt: True}))
