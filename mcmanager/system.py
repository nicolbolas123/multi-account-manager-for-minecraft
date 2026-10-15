"""Which operating system the app runs on, and the wording that depends on it.

Platform-specific *behaviour* lives behind the interfaces in ``mcmanager.windows`` (window
discovery, capture, focus), ``mcmanager.prism.locator``, ``mcmanager.notifications`` and
``mcmanager.paths``. This module only answers "which platform?" and supplies the few
user-facing strings that differ, so UI code doesn't need its own ``sys.platform`` checks.

Supported: macOS (``darwin``) and Windows (``win32``, experimental). Other platforms can run
the UI (and mock mode) but have no window capture or focus backend.
"""

from __future__ import annotations

import sys

IS_MAC = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"

# The OS's settings app and file manager, as named in messages.
SETTINGS_APP = "System Settings" if IS_MAC else "Windows Settings" if IS_WINDOWS else "system settings"
FILE_MANAGER = "Finder" if IS_MAC else "File Explorer" if IS_WINDOWS else "file manager"

# Shortcut labels for tooltips (Qt maps Ctrl to ⌘ on macOS by itself).
MOD_KEY = "⌘" if IS_MAC else "Ctrl+"

# Where Prism Launcher is normally installed, for help texts.
PRISM_INSTALL_HINT = (
    "move it to /Applications" if IS_MAC
    else "install it with its installer (or choose the portable folder's prismlauncher.exe)" if IS_WINDOWS
    else "put prismlauncher on your PATH"
)

# Why a captured window may have no picture, for preview placeholders.
HIDDEN_WINDOW_REASON = "Minimized or on another Space" if IS_MAC else "Minimized"


def needs_privacy_permissions() -> bool:
    """macOS gates window capture and control behind Screen Recording / Accessibility permission.

    Windows has no equivalent prompts: any process can capture and focus the windows of other
    processes of the same user (without administrator rights).
    """
    return IS_MAC
