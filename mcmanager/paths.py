"""Filesystem locations used by the application itself (not by Prism).

User data always lives in the user's Library folders, never inside the application bundle,
so replacing or rebuilding the .app can't affect it. ``MCMANAGER_DATA_DIR`` (testing only)
redirects all of it to one throwaway folder.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from mcmanager import APP_ID


def _override(sub: str) -> Path | None:
    root = os.environ.get("MCMANAGER_DATA_DIR")
    return Path(root) / sub if root else None


def app_support_dir() -> Path:
    if (p := _override("support")) is not None:
        return p
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_ID
    return Path.home() / ".config" / APP_ID


def logs_dir() -> Path:
    if (p := _override("logs")) is not None:
        return p
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / APP_ID
    return app_support_dir() / "logs"


def cache_dir() -> Path:
    if (p := _override("cache")) is not None:
        return p
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / APP_ID
    return Path.home() / ".cache" / APP_ID
