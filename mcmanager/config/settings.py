"""Persisted settings and dashboard entries.

A *dashboard entry* is the user's own record for one account tile: a custom
display name bound to a Prism instance and (optionally) a Prism account
profile. It is deliberately separate from Prism's instance configuration, from
the Minecraft account, and from any running process.

Only non-secret data is stored here. Account *profile names* are stored so we
can pass ``--profile`` to Prism; tokens never leave Prism's own files.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# Palette offered for colour labels (also used as the default accent per tile).
LABEL_COLORS = [
    "#4ade80",  # green
    "#60a5fa",  # blue
    "#f472b6",  # pink
    "#facc15",  # yellow
    "#a78bfa",  # violet
    "#fb923c",  # orange
    "#2dd4bf",  # teal
    "#f87171",  # red
]


@dataclass
class DashboardEntry:
    display_name: str
    prism_instance_id: str
    account_profile: str | None = None  # None = Prism's currently active account
    color: str = LABEL_COLORS[0]
    favorite: bool = False
    group: str = ""  # name of a GroupDef; "" = ungrouped
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: float = field(default_factory=time.time)


@dataclass
class GroupDef:
    """A user-named dashboard section. Entries refer to it by name; order is display order."""

    name: str
    collapsed: bool = False
    pinned: bool = False  # pinned groups always stay above unpinned ones


@dataclass
class PreviewSettings:
    grid_fps: float = 4.0
    grid_max_width: int = 480
    expanded_fps: float = 15.0
    expanded_max_width: int = 1600
    backend: str = "auto"  # auto | screencapturekit | quartz


# Notification categories: key -> (label, description). Order is display order.
NOTIFICATION_CATEGORIES = {
    "crashed": ("Account crashed", "A game process crashed or closed unexpectedly."),
    "disconnected": ("Account disconnected", "The account lost its connection to a server."),
    "launch_failed": ("Launch failed", "An instance could not be launched."),
    "started": ("Account started", "A launched game process is running."),
    "bulk_completed": ("Bulk launch completed", "A multi-account launch finished (including partial failures)."),
    "bulk_failed": ("Bulk launch failed", "Some or all accounts in a multi-account launch failed."),
}


@dataclass
class NotificationSettings:
    enabled: bool = True  # master switch for optional notifications
    crashed: bool = True
    disconnected: bool = False  # not detectable reliably; kept so a future integration has a home
    launch_failed: bool = True
    started: bool = False
    bulk_completed: bool = True
    bulk_failed: bool = True

    def allows(self, category: str) -> bool:
        return self.enabled and bool(getattr(self, category, False))


LAUNCH_DELAY_RANGE = (1, 600)  # seconds; validated on load and in the settings UI


@dataclass
class AppSettings:
    prism_app_path: str | None = None  # override for the .app bundle / executable
    prism_data_dir: str | None = None  # override for Prism's data directory
    tile_width: int = 360
    favorites_first: bool = False  # show favorite accounts at the front of their group
    confirm_stop: bool = True  # ask before Force Quit, Stop All and stopping/restarting a selection
    launch_timeout_s: int = 240
    staggered_launch: bool = False  # launch multi-account requests one after another
    launch_delay_s: int = 20  # minimum gap between staggered launch attempts
    preview: PreviewSettings = field(default_factory=PreviewSettings)
    notifications: NotificationSettings = field(default_factory=NotificationSettings)
    entries: list[DashboardEntry] = field(default_factory=list)  # list order is display order
    groups: list[GroupDef] = field(default_factory=list)

    def entry(self, entry_id: str) -> DashboardEntry | None:
        return next((e for e in self.entries if e.id == entry_id), None)


def _filter_fields(cls, data: dict[str, Any]) -> dict[str, Any]:
    names = {f.name for f in dataclasses.fields(cls)}
    unknown = set(data) - names
    if unknown:
        log.debug("Ignoring unknown %s fields: %s", cls.__name__, sorted(unknown))
    return {k: v for k, v in data.items() if k in names}


def settings_from_dict(data: dict[str, Any]) -> AppSettings:
    data = dict(data)
    data.pop("schema_version", None)
    preview = PreviewSettings(**_filter_fields(PreviewSettings, data.pop("preview", {}) or {}))
    notifications = NotificationSettings(**_filter_fields(NotificationSettings, _as_dict(data.pop("notifications", {}))))
    entries = []
    for raw in data.pop("entries", []) or []:
        try:
            entries.append(DashboardEntry(**_filter_fields(DashboardEntry, raw)))
        except TypeError as exc:
            log.warning("Skipping malformed dashboard entry %r: %s", raw, exc)
    groups = []
    for raw in data.pop("groups", []) or []:
        try:
            groups.append(GroupDef(**_filter_fields(GroupDef, raw)))
        except TypeError as exc:
            log.warning("Skipping malformed group %r: %s", raw, exc)
    settings = AppSettings(preview=preview, notifications=notifications, entries=entries, groups=groups,
                           **_filter_fields(AppSettings, data))
    _sanitize(settings)
    return settings


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _sanitize(settings: AppSettings) -> None:
    """Replace values of the wrong type or out of range with defaults (hand-edited or old files)."""
    defaults = AppSettings()
    for obj, default in ((settings, defaults), (settings.notifications, defaults.notifications)):
        for f in dataclasses.fields(obj):
            value, fallback = getattr(obj, f.name), getattr(default, f.name)
            if isinstance(fallback, bool) and not isinstance(value, bool):
                log.warning("Setting %s=%r is not true/false; using %r", f.name, value, fallback)
                setattr(obj, f.name, fallback)
    lo, hi = LAUNCH_DELAY_RANGE
    delay = settings.launch_delay_s
    if isinstance(delay, bool) or not isinstance(delay, (int, float)) or not lo <= delay <= hi:
        log.warning("Invalid launch delay %r; using %s s", delay, defaults.launch_delay_s)
        settings.launch_delay_s = defaults.launch_delay_s
    else:
        settings.launch_delay_s = int(delay)


def settings_to_dict(settings: AppSettings) -> dict[str, Any]:
    data = dataclasses.asdict(settings)
    data["schema_version"] = SCHEMA_VERSION
    return data


def _replace_with_retry(src: str, dst: Path, attempts: int = 10, delay: float = 0.05) -> None:
    """``os.replace``, retried briefly on Windows, where a virus scanner, search indexer or
    OneDrive can hold the target open for a moment ("Access is denied" / PermissionError)."""
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if sys.platform != "win32" or attempt == attempts - 1:
                raise
            time.sleep(delay * (attempt + 1))


def atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON via a temp file + rename so a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        _replace_with_retry(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_json_or_backup(path: Path) -> Any | None:
    """Read a JSON file. A missing file gives None; an unreadable one is moved aside and gives None."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        backup = path.with_suffix(f".corrupt-{int(time.time())}.json")
        log.error("Could not read %s (%s); moving it to %s", path.name, exc, backup)
        try:
            path.rename(backup)
        except OSError:
            log.exception("Could not back up unreadable file")
        return None


class SettingsStore:
    """Loads and atomically saves :class:`AppSettings` as JSON."""

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> AppSettings:
        if not self.path.exists():
            log.info("No settings file at %s; starting with defaults", self.path)
            return AppSettings()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("top-level JSON value is not an object")
            return settings_from_dict(data)
        except (OSError, ValueError, TypeError) as exc:
            backup = self.path.with_suffix(f".corrupt-{int(time.time())}.json")
            log.error("Could not read settings (%s); moving it to %s", exc, backup)
            try:
                self.path.rename(backup)
            except OSError:
                log.exception("Could not back up unreadable settings file")
            return AppSettings()

    def save(self, settings: AppSettings) -> None:
        atomic_write_json(self.path, settings_to_dict(settings))
