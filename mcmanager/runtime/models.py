"""Runtime (non-persisted) state for dashboard entries."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from mcmanager.windows.discovery import WindowInfo


class RunState(Enum):
    STOPPED = "Stopped"
    LAUNCHING = "Launching"
    RUNNING = "Running"
    STOPPING = "Stopping"
    EXITED = "Exited"
    CRASHED = "Crashed"
    FAILED = "Launch failed"

    @property
    def is_active(self) -> bool:
        """True while a game process exists or is expected to appear."""
        return self in (RunState.LAUNCHING, RunState.RUNNING, RunState.STOPPING)


@dataclass
class LogState:
    """Facts parsed (best effort) from the game's ``logs/latest.log``."""

    username: str | None = None
    connection: str | None = None
    clean_shutdown: bool = False


@dataclass
class RuntimeInfo:
    state: RunState = RunState.STOPPED
    pid: int | None = None
    process_started_at: float | None = None
    launch_requested_at: float | None = None
    stop_requested_at: float | None = None
    window: WindowInfo | None = None
    log: LogState = field(default_factory=LogState)
    cpu_percent: float | None = None
    memory_bytes: int | None = None
    message: str | None = None
    crash_report: str | None = None
    restart_pending: bool = False
    attached: bool = False  # True when the process was already running when we found it


@dataclass(frozen=True)
class AppEvent:
    """Something notification-worthy that happened. ``key`` identifies the event for de-duplication."""

    category: str  # a key of config.settings.NOTIFICATION_CATEGORIES
    title: str
    message: str
    key: str
    entry_id: str | None = None
    also: tuple[str, ...] = ()  # other categories that also cover this event (sent once if any is enabled)
    shown_in_app: bool = False  # the dashboard already showed this as a toast (in-app fallback skips it)

    @property
    def categories(self) -> tuple[str, ...]:
        return (self.category, *self.also)
