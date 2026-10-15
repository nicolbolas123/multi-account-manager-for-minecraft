"""Turns entry + runtime + preview state into display strings (shared by card and detail views)."""

from __future__ import annotations

from dataclasses import dataclass

from mcmanager import system
from mcmanager.config import DashboardEntry
from mcmanager.prism import PrismInstance
from mcmanager.runtime.models import RunState, RuntimeInfo
from mcmanager.runtime.previews import PreviewState
from mcmanager.ui import theme
from mcmanager.ui.widgets import format_bytes


@dataclass
class Presentation:
    status_text: str
    status_color: str
    account_line: str
    instance_line: str
    connection: str
    resources: str
    overlay_title: str
    overlay_subtitle: str
    dim: bool


def present(entry: DashboardEntry, instance: PrismInstance | None, info: RuntimeInfo,
            preview: PreviewState, previews_paused: bool, queue_position: int | None = None) -> Presentation:
    state = info.state
    username = info.log.username
    if username:
        account = username
    elif entry.account_profile:
        account = entry.account_profile
    else:
        account = "Prism's active account"

    if instance:
        instance_line = instance.name + (f"  ·  {instance.summary}" if instance.summary else "")
    else:
        instance_line = f"Missing Prism instance: {entry.prism_instance_id}"

    running = state in (RunState.RUNNING, RunState.STOPPING)
    connection = info.log.connection or ("Connection unknown" if running else "")
    resources = ""
    if running and info.cpu_percent is not None:
        resources = f"CPU {info.cpu_percent:.0f}%  ·  RAM {format_bytes(info.memory_bytes)}"

    title, subtitle, dim = "", "", False
    if state == RunState.STOPPED:
        title = "Stopped" if instance else "Instance not found"
        subtitle = "" if instance else "Edit this entry to choose another Prism instance"
    elif state in (RunState.EXITED, RunState.CRASHED, RunState.FAILED):
        title, subtitle = state.value, info.message or ""
    elif state == RunState.LAUNCHING:
        title, subtitle = "Launching…", info.message or ""
    elif running:
        if previews_paused:
            title, subtitle, dim = "Previews paused", "", True
        elif info.window is None:
            title, subtitle = "Waiting for game window…", "The game is running but has no window yet"
        elif preview.permission_problem:
            title = "Screen Recording permission needed"
            subtitle = f"Enable it in {system.SETTINGS_APP} › Privacy & Security, then restart the app"
        elif preview.frame is None:
            if not info.window.onscreen:
                title, subtitle = "Window hidden", system.HIDDEN_WINDOW_REASON
            elif preview.error:
                title, subtitle = "Preview unavailable", preview.error
            else:
                title = "Connecting preview…"
        elif preview.error or preview.stale:
            dim = True
            title = "Preview paused"
            subtitle = f"Window hidden ({system.HIDDEN_WINDOW_REASON.lower()})" if not info.window.onscreen \
                else (preview.error or "")
        if state == RunState.STOPPING:
            title, subtitle, dim = "Closing…", info.message or "", True

    status = state.value
    color = theme.STATE_COLORS[state]
    if state == RunState.RUNNING and info.attached:
        status = "Running (attached)"
    if queue_position is not None and not state.is_active:
        status = f"Queued for launch (#{queue_position})"
        color = theme.STATE_COLORS[RunState.LAUNCHING]
        title, subtitle, dim = "Queued", f"Number {queue_position} in the launch queue", False
    return Presentation(
        status_text=status,
        status_color=color,
        account_line=account,
        instance_line=instance_line,
        connection=connection,
        resources=resources,
        overlay_title=title,
        overlay_subtitle=subtitle,
        dim=dim,
    )
