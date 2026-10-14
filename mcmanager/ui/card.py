"""Instance tile for the dashboard grid, plus the shared per-entry action menu."""

from __future__ import annotations

import time
from typing import Callable

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QApplication, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QMenu, QVBoxLayout, QWidget

from mcmanager.runtime.manager import InstanceManager
from mcmanager.runtime.models import RunState
from mcmanager.ui.groups import entry_mime
from mcmanager.ui.presenter import Presentation
from mcmanager.ui.widgets import PreviewView, StatusDot, button, repolish

Modifiers = Qt.KeyboardModifier

# A press only becomes a drag once the mouse has been held down for a moment *and* moved clearly,
# so ordinary clicks with a slight wobble select the tile instead of starting a drag.
DRAG_START_DISTANCE = 24  # px (Qt's default is much smaller)
DRAG_START_DELAY_S = 0.15


def build_entry_menu(parent: QWidget, manager: InstanceManager, entry_id: str,
                     emit: Callable[[str, str], None], include_expand: bool = True) -> QMenu:
    entry = manager.entry(entry_id)
    info = manager.info(entry_id)
    menu = QMenu(parent)

    def add(text: str, action: str, enabled: bool = True):
        a = menu.addAction(text)
        a.setEnabled(enabled)
        a.triggered.connect(lambda: emit(entry_id, action))
        return a

    running = info.state in (RunState.RUNNING, RunState.STOPPING)
    add("Launch", "launch", manager.can_launch(entry_id)[0])
    add("Bring Window to Front", "focus", info.pid is not None)
    add("Stop", "stop", info.state == RunState.RUNNING)
    add("Restart", "restart", info.state == RunState.RUNNING)
    add("Force Quit…", "force_quit", running)
    menu.addSeparator()
    if include_expand:
        add("Expand", "expand")
    add("Rename…", "rename")
    add("Edit…", "edit")
    add("Remove from Favorites" if entry.favorite else "Add to Favorites", "toggle_favorite")
    _add_group_menu(menu, manager, entry_id, emit)
    menu.addSeparator()
    add("Open Game Folder", "open_folder", manager.game_dir(entry_id) is not None)
    if info.crash_report:
        add("Open Crash Report", "open_crash")
    add("Show in Prism Launcher", "show_in_prism", manager.instance_for(entry) is not None)
    menu.addSeparator()
    add("Remove from Dashboard…", "remove")
    return menu


def _add_group_menu(menu: QMenu, manager: InstanceManager, entry_id: str, emit: Callable[[str, str], None]) -> None:
    current = manager.entry(entry_id).group
    sub = menu.addMenu("Move to Group")

    def add(text: str, action: str, checked: bool | None = None):
        a = sub.addAction(text)
        if checked is not None:
            a.setCheckable(True)
            a.setChecked(checked)
        a.triggered.connect(lambda: emit(entry_id, action))

    for group in manager.groups:
        add(group.name, "group:" + group.name, group.name == current)
    if manager.groups:
        sub.addSeparator()
    add("No Group", "group:", not current)
    add("New Group…", "group_new")


class InstanceCard(QFrame):
    """One account tile.

    Mouse handling: ``pressed`` / ``clicked`` carry the keyboard modifiers so the main
    window can apply ⌘/Ctrl/Shift multi-selection. ``clicked`` fires on release when no
    drag happened. ``drag_ids`` (set by the owner) returns what a drag carries, so
    dragging a selected tile moves the whole selection.
    """

    pressed = Signal(str, object)  # entry_id, Qt.KeyboardModifier
    clicked = Signal(str, object)
    context_requested = Signal(str)
    action = Signal(str, str)  # entry_id, action name
    drag_started = Signal(str)
    drag_finished = Signal(str)

    def __init__(self, entry_id: str, manager: InstanceManager):
        super().__init__()
        self.entry_id = entry_id
        self.manager = manager
        self._press_pos: QPoint | None = None
        self._press_time = 0.0
        self._press_mods = Modifiers.NoModifier
        self.drag_ids: Callable[[str], list[str]] = lambda entry_id: [entry_id]
        self.setObjectName("Card")
        self.setProperty("selected", False)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 12)
        root.setSpacing(8)

        self.preview = PreviewView()  # its presses propagate to this card (see mousePressEvent)
        self.preview.double_clicked.connect(lambda: self.action.emit(self.entry_id, "expand"))
        root.addWidget(self.preview)

        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        self.check = QLabel("✓")
        self.check.setObjectName("SelectBadge")
        self.check.setFixedSize(18, 18)
        self.check.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.check.setToolTip("Selected")
        self.check.hide()
        name_row.addWidget(self.check)
        self.color_bar = QFrame()
        self.color_bar.setFixedSize(4, 18)
        name_row.addWidget(self.color_bar)
        self.name = QLabel()
        self.name.setObjectName("CardName")
        name_row.addWidget(self.name, 1)
        self.fav = QLabel("★")
        self.fav.setStyleSheet("color: #facc15; font-size: 14px;")
        name_row.addWidget(self.fav)
        self.menu_button = button("⋯", "ghost", "More actions")
        self.menu_button.setFixedWidth(32)
        self.menu_button.clicked.connect(self._show_menu)
        name_row.addWidget(self.menu_button)
        root.addLayout(name_row)

        self.account = QLabel()
        self.account.setObjectName("CardSub")
        root.addWidget(self.account)

        status_row = QHBoxLayout()
        status_row.setSpacing(6)
        self.dot = StatusDot()
        status_row.addWidget(self.dot)
        self.status = QLabel()
        self.status.setObjectName("CardSub")
        status_row.addWidget(self.status)
        status_row.addStretch(1)
        self.connection = QLabel()
        self.connection.setObjectName("CardMeta")
        status_row.addWidget(self.connection)
        root.addLayout(status_row)

        self.meta = QLabel()
        self.meta.setObjectName("CardMeta")
        root.addWidget(self.meta)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.primary = button("Launch", "primary")
        self.primary.clicked.connect(self._primary_clicked)
        buttons.addWidget(self.primary, 1)
        self.focus_btn = button("Bring to Front", None, "Bring the real Minecraft window to the foreground")
        self.focus_btn.clicked.connect(lambda: self.action.emit(self.entry_id, "focus"))
        buttons.addWidget(self.focus_btn, 1)
        self.expand_btn = button("⤢", None, "Expand preview")
        self.expand_btn.setFixedWidth(38)
        self.expand_btn.clicked.connect(lambda: self.action.emit(self.entry_id, "expand"))
        buttons.addWidget(self.expand_btn)
        root.addLayout(buttons)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
            self._press_time = time.monotonic()
            self._press_mods = event.modifiers()
            self.pressed.emit(self.entry_id, event.modifiers())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_pos is None or not event.buttons() & Qt.MouseButton.LeftButton:
            return super().mouseMoveEvent(event)
        moved = (event.position().toPoint() - self._press_pos).manhattanLength()
        held = time.monotonic() - self._press_time
        if moved >= max(DRAG_START_DISTANCE, QApplication.startDragDistance()) and held >= DRAG_START_DELAY_S:
            hotspot, self._press_pos = self._press_pos, None
            self.start_drag(hotspot)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._press_pos is not None:
            self.clicked.emit(self.entry_id, self._press_mods)  # a click, not the end of a drag
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def start_drag(self, hotspot: QPoint) -> None:
        """Drag this card (by entry id) to another position or group. Blocks until dropped."""
        pixmap = self.grab()
        ratio = pixmap.devicePixelRatio()
        scale = min(1.0, 260 / max(1, self.width()))
        pixmap = pixmap.scaledToWidth(int(self.width() * scale * ratio), Qt.TransformationMode.SmoothTransformation)
        pixmap.setDevicePixelRatio(ratio)
        ids = self.drag_ids(self.entry_id) or [self.entry_id]
        drag = QDrag(self)
        drag.setMimeData(entry_mime(ids))
        drag.setPixmap(pixmap)
        drag.setHotSpot(hotspot * scale)
        fade = QGraphicsOpacityEffect(self)
        fade.setOpacity(0.35)
        self.setGraphicsEffect(fade)
        self.drag_started.emit(self.entry_id)
        try:
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            self.setGraphicsEffect(None)
            self.drag_finished.emit(self.entry_id)

    def mouseDoubleClickEvent(self, event):
        self.action.emit(self.entry_id, "expand")

    def contextMenuEvent(self, event):
        self.context_requested.emit(self.entry_id)
        build_entry_menu(self, self.manager, self.entry_id, self.action.emit).exec(event.globalPos())

    def _show_menu(self):
        menu = build_entry_menu(self, self.manager, self.entry_id, self.action.emit)
        menu.exec(self.menu_button.mapToGlobal(self.menu_button.rect().bottomLeft()))

    def _primary_clicked(self):
        if self.manager.queue.is_waiting(self.entry_id):
            return self.action.emit(self.entry_id, "cancel_queued")
        state = self.manager.info(self.entry_id).state
        self.action.emit(self.entry_id, "stop" if state == RunState.RUNNING else "launch")

    def set_selected(self, selected: bool) -> None:
        self.check.setVisible(selected)
        if self.property("selected") != selected:
            self.setProperty("selected", selected)
            repolish(self)

    def update_view(self, pres: Presentation, preview_frame, entry) -> None:
        self.name.setText(entry.display_name)
        self.name.setToolTip(entry.display_name)
        self.fav.setVisible(entry.favorite)
        self.color_bar.setStyleSheet(f"background: {entry.color}; border-radius: 2px;")
        self.account.setText(pres.account_line)
        self.status.setText(pres.status_text)
        self.status.setToolTip(self.manager.info(self.entry_id).message or "")
        self.dot.set_color(pres.status_color)
        self.connection.setText(pres.connection)
        self.connection.setToolTip("Best-effort, from the game's log file")
        self.meta.setText(pres.resources or pres.instance_line)
        self.meta.setToolTip(pres.instance_line)
        self.preview.set_accent(entry.color)
        self.preview.set_frame(preview_frame, pres.dim)
        self.preview.set_overlay(pres.overlay_title, pres.overlay_subtitle)

        info = self.manager.info(self.entry_id)
        if self.manager.queue.is_waiting(self.entry_id):
            self.primary.setText("Cancel Queued Launch")
            self.primary.setProperty("kind", None)
            self.primary.setEnabled(True)
        elif info.state == RunState.RUNNING:
            self.primary.setText("Stop")
            self.primary.setProperty("kind", "danger")
            self.primary.setEnabled(True)
        elif info.state.is_active:
            self.primary.setText(info.state.value + "…")
            self.primary.setProperty("kind", "primary")
            self.primary.setEnabled(False)
        else:
            self.primary.setText("Launch")
            self.primary.setProperty("kind", "primary")
            self.primary.setEnabled(self.manager.can_launch(self.entry_id)[0])
        repolish(self.primary)
        self.focus_btn.setEnabled(info.pid is not None)
