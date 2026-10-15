"""Filesystem locations used by the application itself (not by Prism).

User data always lives in the user's profile, never inside the application folder or bundle,
so replacing, upgrading or rebuilding the app can't affect it:

* macOS:   ``~/Library/Application Support|Logs|Caches/<APP_ID>``
* Windows: ``%APPDATA%\\<APP_ID>`` (settings, play time; roams with the profile) and
  ``%LOCALAPPDATA%\\<APP_ID>\\Logs|Cache`` (machine-specific)
* others:  ``~/.config``, ``~/.cache``

``MCMANAGER_DATA_DIR`` (testing only) redirects all of it to one throwaway folder.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from mcmanager import APP_ID


def _override(sub: str) -> Path | None:
    root = os.environ.get("MCMANAGER_DATA_DIR")
    return Path(root) / sub if root else None


def _windows_dir(variable: str, fallback: str) -> Path:
    """A Windows known folder from the environment, with the standard default if it's unset."""
    value = os.environ.get(variable)
    return Path(value) if value else Path.home() / fallback


def app_support_dir() -> Path:
    if (p := _override("support")) is not None:
        return p
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_ID
    if sys.platform == "win32":
        return _windows_dir("APPDATA", "AppData/Roaming") / APP_ID
    return Path.home() / ".config" / APP_ID


def logs_dir() -> Path:
    if (p := _override("logs")) is not None:
        return p
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / APP_ID
    if sys.platform == "win32":
        return _windows_dir("LOCALAPPDATA", "AppData/Local") / APP_ID / "Logs"
    return app_support_dir() / "logs"


def cache_dir() -> Path:
    if (p := _override("cache")) is not None:
        return p
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / APP_ID
    if sys.platform == "win32":
        return _windows_dir("LOCALAPPDATA", "AppData/Local") / APP_ID / "Cache"
    return Path.home() / ".cache" / APP_ID
