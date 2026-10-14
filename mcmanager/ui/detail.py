"""Expanded single-instance view."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from mcmanager.runtime.manager import InstanceManager
from mcmanager.runtime.models import RunState
from mcmanager.runtime.playtime import format_duration
from mcmanager.ui.card import build_entry_menu
from mcmanager.ui.presenter import Presentation
from mcmanager.ui.widgets import PreviewView, StatusDot, button, format_bytes, repolish, section_label


def format_when(ts: float | None) -> str:
    if not ts:
        return "—"
    dt = datetime.fromtimestamp(ts)
    today = datetime.now().date()
    day = "Today" if dt.date() == today else f"{dt:%b} {dt.day}" + (f", {dt.year}" if dt.year != today.year else "")
    return f"{day}, {dt:%H:%M}"


class DetailView(QWidget):
    back = Signal()
    action = Signal(str, str)

    def __init__(self, manager: InstanceManager):
        super().__init__()
        self.manager = manager
        self.entry_id: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 20)
        root.setSpacing(14)

        top = QHBoxLayout()
        self.back_btn = button("‹  Back to Grid", "ghost", "Return to the dashboard (Esc)")
        self.back_btn.clicked.connect(self.back.emit)
        top.addWidget(self.back_btn)
        top.addSpacing(8)
        self.color_bar = QFrame()
        self.color_bar.setFixedSize(5, 26)
        top.addWidget(self.color_bar)
        self.title = QLabel()
        self.title.setObjectName("DetailTitle")
        top.addWidget(self.title)
        self.selected_tag = QLabel("SELECTED")
        self.selected_tag.setObjectName("MockBadge")
        self.selected_tag.setStyleSheet("background:#16244a; color:#9db8ff; border-color:#2b4387;")
        top.addWidget(self.selected_tag)
        top.addStretch(1)
        self.dot = StatusDot(12)
        top.addWidget(self.dot)
        self.status = QLabel()
        top.addWidget(self.status)
        root.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(16)
        self.preview = PreviewView(radius=12, min_height=240)
        self.preview.setToolTip("Double-click to bring the real game window to the front")
        self.preview.double_clicked.connect(lambda: self._emit("focus"))
        body.addWidget(self.preview, 1)

        panel = QFrame()
        panel.setObjectName("Panel")
        panel.setFixedWidth(320)
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(16, 16, 16, 16)
        pl.setSpacing(10)

        self.focus_btn = button("Bring Window to Front", "primary")
        self.focus_btn.clicked.connect(lambda: self._emit("focus"))
        pl.addWidget(self.focus_btn)
        self.primary = button("Launch")
        self.primary.clicked.connect(self._primary_clicked)
        pl.addWidget(self.primary)
        row = QHBoxLayout()
        self.restart_btn = button("Restart")
        self.restart_btn.clicked.connect(lambda: self._emit("restart"))
        row.addWidget(self.restart_btn)
        self.kill_btn = button("Force Quit", "danger")
        self.kill_btn.clicked.connect(lambda: self._emit("force_quit"))
        row.addWidget(self.kill_btn)
        pl.addLayout(row)
        row2 = QHBoxLayout()
        edit_btn = button("Edit…")
        edit_btn.clicked.connect(lambda: self._emit("edit"))
        row2.addWidget(edit_btn)
        self.more_btn = button("More ▾")
        self.more_btn.clicked.connect(self._show_menu)
        row2.addWidget(self.more_btn)
        pl.addLayout(row2)
        pl.addSpacing(8)

        self.fields: dict[str, QLabel] = {}
        pl.addLayout(self._field_grid([
            ("account", "Account"), ("instance", "Instance"), ("connection", "Connection"),
            ("pid", "Process"), ("cpu", "CPU"), ("ram", "Memory"), ("window", "Window"), ("message", "Note"),
        ]))
        pl.addSpacing(6)
        pl.addWidget(section_label("Play time"))
        pl.addLayout(self._field_grid([
            ("played", "Total"), ("current", "This session"), ("last_session", "Last session"),
            ("last_launch", "Last launched"), ("prism_played", "Prism counter"),
        ]))
        self.fields["played"].setToolTip("Time this account's game process has been running, across all sessions")
        self.fields["prism_played"].setToolTip(
            "Prism Launcher's own total for this instance (all accounts that use it, launches Prism tracked). "
            "Shown for reference; the totals above are tracked by this app per account.")
        pl.addStretch(1)
        hint = QLabel("Previews are view-only. Use “Bring Window to Front” to play with your normal mouse and keyboard.")
        hint.setObjectName("CardMeta")
        hint.setWordWrap(True)
        pl.addWidget(hint)
        body.addWidget(panel)
        root.addLayout(body, 1)

    def _field_grid(self, rows: list[tuple[str, str]]) -> QGridLayout:
        grid = QGridLayout()
        grid.setVerticalSpacing(10)
        grid.setHorizontalSpacing(8)
        for i, (key, label) in enumerate(rows):
            name = QLabel(label)
            name.setObjectName("FieldLabel")
            value = QLabel("—")
            value.setObjectName("FieldValue")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            name.setFixedWidth(92)
            grid.addWidget(name, i, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(value, i, 1, Qt.AlignmentFlag.AlignTop)
            self.fields[key] = value
        return grid

    def update_playtime(self) -> None:
        """Refresh the play-time fields (called every second while the view is shown)."""
        entry = self.manager.entry(self.entry_id) if self.entry_id else None
        if entry is None:
            return
        pt = self.manager.playtime
        self.fields["played"].setText(format_duration(pt.total_s(entry.id)))
        current = pt.current_session_s(entry.id)
        self.fields["current"].setText(format_duration(current) if current is not None else "Not running")
        last = pt.last_session(entry.id)
        if last:
            note = " (estimated end)" if last.estimated else ""
            self.fields["last_session"].setText(f"{format_duration(last.duration)}{note}\n"
                                                f"{format_when(last.start)} – {format_when(last.end)}")
        else:
            self.fields["last_session"].setText("—")
        self.fields["last_launch"].setText(format_when(pt.last_launch_requested(entry.id)))
        inst = self.manager.instance_for(entry)
        prism_total = inst.prism_total_played_s if inst else None
        self.fields["prism_played"].setText(
            f"{format_duration(prism_total)} (instance)" if prism_total is not None else "—")

    def _emit(self, action: str) -> None:
        if self.entry_id:
            self.action.emit(self.entry_id, action)

    def _primary_clicked(self) -> None:
        if self.entry_id:
            state = self.manager.info(self.entry_id).state
            self._emit("stop" if state == RunState.RUNNING else "launch")

    def _show_menu(self) -> None:
        if self.entry_id:
            menu = build_entry_menu(self, self.manager, self.entry_id, self.action.emit, include_expand=False)
            menu.exec(self.more_btn.mapToGlobal(self.more_btn.rect().bottomLeft()))

    def show_entry(self, entry_id: str) -> None:
        self.entry_id = entry_id

    def update_view(self, pres: Presentation, frame) -> None:
        entry = self.manager.entry(self.entry_id) if self.entry_id else None
        if entry is None:
            return
        info = self.manager.info(entry.id)
        inst = self.manager.instance_for(entry)
        self.title.setText(entry.display_name + ("  ★" if entry.favorite else ""))
        self.color_bar.setStyleSheet(f"background: {entry.color}; border-radius: 2px;")
        self.dot.set_color(pres.status_color)
        self.status.setText(pres.status_text)
        self.preview.set_frame(frame, pres.dim)
        self.preview.set_overlay(pres.overlay_title, pres.overlay_subtitle)

        profile = entry.account_profile or "Prism's active account"
        username = info.log.username
        self.fields["account"].setText(f"{username} (from game log)" if username else profile)
        self.fields["instance"].setText(pres.instance_line)
        self.fields["instance"].setToolTip(str(inst.path) if inst else "")
        self.fields["connection"].setText(pres.connection or "—")
        self.fields["pid"].setText(str(info.pid) if info.pid else "—")
        self.fields["cpu"].setText(f"{info.cpu_percent:.0f}%" if info.cpu_percent is not None else "—")
        self.fields["ram"].setText(format_bytes(info.memory_bytes) if info.memory_bytes else "—")
        w = info.window
        self.fields["window"].setText(
            f"{w.width}×{w.height}{'' if w.onscreen else ' (hidden)'}" if w else "—"
        )
        self.fields["message"].setText(info.message or "—")
        self.update_playtime()

        running = info.state == RunState.RUNNING
        self.focus_btn.setEnabled(info.pid is not None)
        if running:
            self.primary.setText("Stop")
            self.primary.setProperty("kind", "danger")
            self.primary.setEnabled(True)
        elif info.state.is_active:
            self.primary.setText(info.state.value + "…")
            self.primary.setProperty("kind", None)
            self.primary.setEnabled(False)
        else:
            self.primary.setText("Launch")
            self.primary.setProperty("kind", None)
            self.primary.setEnabled(self.manager.can_launch(entry.id)[0])
        repolish(self.primary)
        self.restart_btn.setEnabled(running or not info.state.is_active)
        self.kill_btn.setEnabled(info.state in (RunState.RUNNING, RunState.STOPPING))
