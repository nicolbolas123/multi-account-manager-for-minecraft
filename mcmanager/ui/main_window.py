"""Main dashboard window: header, banners, instance grid and expanded view."""

from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl
from PySide6.QtGui import QCursor, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QApplication,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QInputDialog,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from mcmanager import APP_NAME, DISCORD_USERNAME, ISSUES_URL, system
from mcmanager.runtime.manager import InstanceManager
from mcmanager.runtime.models import RunState
from mcmanager.runtime.previews import PreviewCoordinator
from mcmanager.runtime.playtime import format_duration
from mcmanager.ui.bulk import BulkBar, QueueBar
from mcmanager.ui.card import InstanceCard
from mcmanager.ui.detail import DetailView
from mcmanager.ui.dialogs import EntryDialog, SettingsDialog
from mcmanager.ui.groups import SECTION_PAD, GroupSection
from mcmanager.ui.presenter import present
from mcmanager.ui.selection import Selection
from mcmanager.ui.widgets import Banner, ElidedLabel, ToastArea, button
from mcmanager.windows import permissions

log = logging.getLogger(__name__)

FEEDBACK_DISCORD = DISCORD_USERNAME


class MainWindow(QMainWindow):
    def __init__(self, manager: InstanceManager, previews: PreviewCoordinator, mock: bool = False):
        super().__init__()
        self.manager = manager
        self.previews = previews
        self.mock = mock
        self.cards: dict[str, InstanceCard] = {}
        self.sections: list[GroupSection] = []
        self._dragging = False
        self.selection = Selection()
        self._display_order: list[str] = []  # visible tiles, top to bottom, for Shift-click ranges
        self._deferred_click: str | None = None
        self.notifier = None  # set by app.build(); used by Settings for the test button
        self._columns = 0

        self.setWindowTitle(APP_NAME + (" — MOCK DATA" if mock else ""))
        self.resize(1280, 820)
        self.setMinimumSize(1040, 560)

        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_header())

        self.banner_box = QVBoxLayout()
        self.banner_box.setContentsMargins(20, 12, 20, 0)
        self.banner_box.setSpacing(8)
        outer.addLayout(self.banner_box)

        bars = QVBoxLayout()
        bars.setContentsMargins(20, 10, 20, 0)
        bars.setSpacing(8)
        self.queue_bar = QueueBar()
        self.queue_bar.cancel_requested.connect(self._cancel_queue)
        bars.addWidget(self.queue_bar)
        self.bulk_bar = BulkBar()
        self.bulk_bar.requested.connect(self.bulk_action)
        bars.addWidget(self.bulk_bar)
        outer.addLayout(bars)

        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self.stack.addWidget(self._build_grid_page())
        self.detail = DetailView(manager)
        self.detail.back.connect(self.show_grid)
        self.detail.action.connect(self.handle_action)
        self.stack.addWidget(self.detail)

        self.toasts = ToastArea(root)
        self.toasts.hide()

        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self._escape)
        QShortcut(QKeySequence.StandardKey.SelectAll, self, activated=self.select_all_visible)
        QShortcut(QKeySequence(Qt.Key.Key_Return), self, activated=self._expand_selected)
        QShortcut(QKeySequence("Ctrl+N"), self, activated=self.add_entry)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=lambda: self.search.setFocus())

        manager.entries_changed.connect(self.rebuild_grid)
        manager.entry_updated.connect(self.refresh_entry)
        manager.notice.connect(self.toasts.show_message)
        manager.prism_changed.connect(self.refresh_banners)
        previews.preview_updated.connect(self.refresh_entry)
        previews.permission_problem_changed.connect(lambda _v: self.refresh_banners())
        manager.queue.changed.connect(self._on_queue_changed)

        # Once a second: play-time counters, queue countdown.
        self._tick_timer = QTimer(self, interval=1000, timeout=self._tick)
        self._tick_timer.start()

        # Re-check permissions periodically (cheap) so the banner reflects reality.
        self._perm_timer = QTimer(self, interval=5000, timeout=self.refresh_banners)
        self._perm_timer.start()

        self.rebuild_grid()
        self.refresh_banners()

    # ---------------------------------------------------------------- building
    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("Header")
        row = QHBoxLayout(header)
        row.setContentsMargins(20, 14, 20, 14)
        row.setSpacing(10)
        title = ElidedLabel(APP_NAME, min_width=110)  # shortens with "…" when the window is narrow
        title.setObjectName("AppTitle")
        title.setToolTip(APP_NAME)
        row.addWidget(title)
        if self.mock:
            badge = QLabel("MOCK DATA")
            badge.setObjectName("MockBadge")
            badge.setToolTip("Running with simulated instances, processes and previews (--mock).")
            row.addWidget(badge)
        row.addSpacing(12)
        stats = QVBoxLayout()  # two short lines keep the crowded header narrow
        stats.setSpacing(0)
        self.stat_label = QLabel()
        self.stat_label.setObjectName("HeaderStat")
        stats.addWidget(self.stat_label)
        self.playtime_label = QLabel()
        self.playtime_label.setObjectName("HeaderStat")
        self.playtime_label.setToolTip("Combined play time of all accounts on the dashboard (sum of each "
                                       "account's game-process running time, including stopped accounts)")
        stats.addWidget(self.playtime_label)
        row.addLayout(stats)
        row.addStretch(1)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search…")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(90)  # shrinks before anything overlaps in a narrow window
        self.search.setMaximumWidth(150)
        self.search.textChanged.connect(lambda _t: self.rebuild_grid())
        self.search.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        row.addWidget(self.search)

        self.pause_btn = button("Pause Previews", None, "Stop capturing windows to save resources")
        self.pause_btn.setCheckable(True)
        self.pause_btn.toggled.connect(self._toggle_pause)
        row.addWidget(self.pause_btn)
        self.launch_all_btn = button("Launch All", None, "Launch every stopped instance (staggered)")
        self.launch_all_btn.clicked.connect(self.launch_all)
        row.addWidget(self.launch_all_btn)
        self.stop_all_btn = button("Stop All", "danger", "Close every running instance")
        self.stop_all_btn.clicked.connect(self.stop_all)
        row.addWidget(self.stop_all_btn)
        settings_btn = button("Settings", None)
        settings_btn.clicked.connect(self.open_settings)
        row.addWidget(settings_btn)
        feedback_btn = button("Feedback", None, "Suggestions or bug reports")
        feedback_btn.setMenu(self._build_feedback_menu(feedback_btn))
        row.addWidget(feedback_btn)
        self.add_btn = button("+ Add Account", "primary", f"Add a dashboard entry for a Prism instance ({system.MOD_KEY}N)")
        self.add_btn.clicked.connect(self.add_entry)
        row.addWidget(self.add_btn)
        self._header = header
        return header

    def _build_feedback_menu(self, parent: QWidget) -> QMenu:
        menu = QMenu(parent)
        # Plain text row (not clickable), a lighter grey than disabled menu items so it reads easily.
        label = QLabel(f"Suggestions or bugs? Open an issue on GitHub\nor DM {FEEDBACK_DISCORD} on Discord")
        label.setObjectName("FeedbackText")
        info = QWidgetAction(menu)
        info.setDefaultWidget(label)
        menu.addAction(info)
        menu.addSeparator()
        issue = menu.addAction("Open an Issue on GitHub")
        issue.setToolTip(ISSUES_URL)
        issue.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(ISSUES_URL)))
        copy = menu.addAction("Copy Discord username")
        copy.triggered.connect(self._copy_feedback_contact)
        return menu

    def _copy_feedback_contact(self) -> None:
        QApplication.clipboard().setText(FEEDBACK_DISCORD)
        self.toasts.show_message("info", f"Copied “{FEEDBACK_DISCORD}” to the clipboard.")

    def _build_grid_page(self) -> QWidget:
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.grid_host = QWidget()
        self.grid_host.setObjectName("GridHost")
        # Tiles accept their own clicks, so a click that reaches the grid host landed on empty
        # space (or a group header / empty group area): that clears the selection.
        self.grid_host.installEventFilter(self)
        self.grid_host.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.grid_host.customContextMenuRequested.connect(
            lambda p: self._show_space_menu(self.grid_host.mapToGlobal(p)))
        host_layout = QVBoxLayout(self.grid_host)
        host_layout.setContentsMargins(20 - SECTION_PAD, 16 - SECTION_PAD, 20 - SECTION_PAD, 20)
        host_layout.setSpacing(6)
        self.sections_box = QVBoxLayout()
        self.sections_box.setSpacing(6)
        host_layout.addLayout(self.sections_box)
        self.empty = self._build_empty_state()
        host_layout.addWidget(self.empty)
        host_layout.addStretch(1)
        self.scroll.setWidget(self.grid_host)
        # Qt does not auto-scroll a scroll area during drag and drop; do it while a card is dragged.
        self._drag_scroll = QTimer(self, interval=30, timeout=self._drag_autoscroll)
        return self.scroll

    def _build_empty_state(self) -> QWidget:
        w = QFrame()
        w.setObjectName("Panel")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(40, 40, 40, 40)
        lay.setSpacing(10)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = QLabel("⛏")
        icon.setStyleSheet("font-size: 44px;")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(icon)
        self.empty_title = QLabel("No accounts on your dashboard yet")
        self.empty_title.setObjectName("EmptyTitle")
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.empty_title)
        self.empty_text = QLabel()
        self.empty_text.setObjectName("EmptyText")
        self.empty_text.setWordWrap(True)
        self.empty_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_text.setFixedWidth(560)
        lay.addWidget(self.empty_text, 0, Qt.AlignmentFlag.AlignHCenter)
        row = QHBoxLayout()
        row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.import_btn = button("Add All Prism Instances", "primary")
        self.import_btn.clicked.connect(self.import_all)
        row.addWidget(self.import_btn)
        add = button("Add One…")
        add.clicked.connect(self.add_entry)
        row.addWidget(add)
        cfg = button("Configure Prism…")
        cfg.clicked.connect(self.open_settings)
        row.addWidget(cfg)
        lay.addLayout(row)
        return w

    # ------------------------------------------------------------------- grid
    def _matches_search(self, entry) -> bool:
        query = self.search.text().strip().lower()
        if not query:
            return True
        inst = self.manager.instance_for(entry)
        hay = " ".join(filter(None, [
            entry.display_name, entry.group, entry.account_profile, entry.prism_instance_id,
            inst.name if inst else "", self.manager.info(entry.id).log.username,
        ])).lower()
        return query in hay

    def _card(self, entry_id: str) -> InstanceCard:
        card = self.cards.get(entry_id)
        if card is None:
            card = InstanceCard(entry_id, self.manager)
            card.pressed.connect(self._on_card_pressed)
            card.clicked.connect(self._on_card_clicked)
            card.context_requested.connect(self._on_card_context)
            card.drag_ids = self._drag_ids
            card.action.connect(self.handle_action)
            card.drag_started.connect(self._on_drag_started)
            card.drag_finished.connect(self._on_drag_finished)
            self.cards[entry_id] = card
        return card

    def rebuild_grid(self) -> None:
        if self._dragging:
            # Rebuilding would reparent the card being dragged; _on_drag_finished rebuilds.
            return
        for entry_id in list(self.cards):
            if self.manager.entry(entry_id) is None:
                self.cards.pop(entry_id).deleteLater()
        # Detach cards before their old sections are deleted (deleting a section deletes its children).
        for card in self.cards.values():
            card.hide()
            card.setParent(self.grid_host)
        for section in self.sections:
            self.sections_box.removeWidget(section)
            section.deleteLater()
        self.sections = []

        m = self.manager
        searching = bool(self.search.text().strip())
        cols = self._column_count()
        self._columns = cols
        has_groups = bool(m.groups)
        self._display_order = []
        # Groups first (pinned ones lead the list), then accounts that are in no group.
        layout = [(g.name, g.name, g.collapsed, g.pinned) for g in m.groups] + [("", "Ungrouped", False, False)]
        for name, title, collapsed, pinned in layout:
            members = m.display_entries(name)
            visible = [e for e in members if self._matches_search(e)]
            if name and searching and not visible:
                continue  # hide groups without matches while searching
            section = GroupSection(name, title, collapsed and not searching, show_header=has_groups, pinned=pinned)
            section.dropped.connect(self._on_drop)
            section.collapse_toggled.connect(m.set_group_collapsed)
            section.menu_requested.connect(self._show_group_menu)
            section.rename_requested.connect(self.rename_group)
            cards = [self._card(e.id) for e in visible]
            if not (collapsed and not searching):
                self._display_order += [e.id for e in visible]
            for card in cards:
                card.setFixedWidth(m.settings.tile_width)
            section.set_cards(cards, cols, len(members))
            # The ungrouped section only shows when it has cards, or as a drop target while dragging.
            section.setVisible(bool(name) or bool(visible))
            self.sections_box.addWidget(section)
            self.sections.append(section)
        for entry in m.entries:
            self.refresh_entry(entry.id)

        has_entries = bool(m.entries)
        self.empty.setVisible(not has_entries and not has_groups)
        if not has_entries:
            self._update_empty_state()
        self.selection.prune({e.id for e in m.entries})
        if self.stack.currentWidget() is self.detail and m.entry(self.detail.entry_id) is None:
            self.show_grid()
        self._update_header()
        self._update_bars()

    def _column_count(self) -> int:
        available = self.scroll.viewport().width() - 40
        tile = self.manager.settings.tile_width + 16
        return max(1, (available + 16) // tile)

    # ------------------------------------------------------------- groups/drag
    def _on_drag_started(self, _entry_id: str) -> None:
        self._dragging = True
        for section in self.sections:
            if not section.group:
                section.setVisible(True)  # so cards can be dropped out of their group
        self._drag_scroll.start()

    def _on_drag_finished(self, _entry_id: str) -> None:
        self._drag_scroll.stop()
        self._dragging = False
        QTimer.singleShot(0, self.rebuild_grid)

    def _drag_autoscroll(self) -> None:
        viewport = self.scroll.viewport()
        y = viewport.mapFromGlobal(QCursor.pos()).y()
        bar = self.scroll.verticalScrollBar()
        edge = 48
        if y < edge:
            bar.setValue(bar.value() - max(4, (edge - y) // 2))
        elif y > viewport.height() - edge:
            bar.setValue(bar.value() + max(4, (y - viewport.height() + edge) // 2))

    def _on_drop(self, entry_ids: list[str], group: str, before_id: str) -> None:
        self.manager.move_entries(entry_ids, group, before_id or None)

    def _drag_ids(self, entry_id: str) -> list[str]:
        """Dragging a tile that is part of a multi-selection moves the whole selection."""
        if entry_id in self.selection and len(self.selection) > 1:
            return self.selection.ordered(self._display_order)
        return [entry_id]

    def _prompt_group_name(self, title: str, text: str) -> str | None:
        name, ok = QInputDialog.getText(self, title, "Group name:", text=text)
        return name.strip() if ok and name.strip() else None

    def add_group(self) -> str | None:
        name = self._prompt_group_name("New Group", self.manager.unique_group_name("New Group"))
        return self.manager.add_group(name) if name else None

    def rename_group(self, group: str) -> None:
        name = self._prompt_group_name("Rename Group", group)
        if name and name != group and not self.manager.rename_group(group, name):
            self.toasts.show_message("warning", f"There is already a group called “{name}”.")

    def delete_group(self, group: str) -> None:
        n = len(self.manager.group_entries(group))
        detail = (f"Its {n} account{'s' if n != 1 else ''} will move to Ungrouped. Nothing else is removed."
                  if n else "The group is empty.")
        if self._confirm(f"Delete the group “{group}”?", detail):
            self.manager.remove_group(group)

    def _show_space_menu(self, pos) -> None:
        """Right-click on empty dashboard space."""
        self.build_space_menu().exec(pos)

    def build_space_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.addAction("Add Group…", self.add_group)
        menu.addAction("Add Account…", self.add_entry)
        if self.manager.unused_instances():
            menu.addAction("Add All Prism Instances", self.import_all)
        if self.manager.groups:
            menu.addSeparator()
            menu.addAction("Collapse All Groups", lambda: self._collapse_all(True))
            menu.addAction("Expand All Groups", lambda: self._collapse_all(False))
        return menu

    def _show_group_menu(self, group: str, pos) -> None:
        menu = self.build_group_menu(group) if group else self.build_space_menu()
        menu.exec(pos)

    def build_group_menu(self, group: str) -> QMenu:
        m = self.manager
        ids = [e.id for e in m.group_entries(group)]
        info = m.group(group)
        menu = QMenu(self)
        menu.addAction("Rename Group…", lambda: self.rename_group(group))
        launch = menu.addAction("Launch Group", lambda: m.launch_many([i for i in self._display_order if i in ids]
                                                                       + [i for i in ids if i not in self._display_order]))
        launch.setEnabled(any(m.can_launch(i)[0] for i in ids))
        running = [i for i in ids if m.info(i).state == RunState.RUNNING]
        stop = menu.addAction("Stop Group", lambda: [m.stop(i) for i in running])
        stop.setEnabled(bool(running))
        menu.addAction("Expand" if info.collapsed else "Collapse",
                       lambda: m.set_group_collapsed(group, not info.collapsed))
        menu.addSeparator()
        menu.addAction("Unpin from Top" if info.pinned else "Pin to Top",
                       lambda: m.set_group_pinned(group, not info.pinned))
        n = len(m.groups)
        for text, offset in (("Move Up", -1), ("Move Down", 1), ("Move to Top", -n), ("Move to Bottom", n)):
            act = menu.addAction(text, lambda *_, o=offset: m.move_group(group, o))
            act.setEnabled(m.can_move_group(group, -1 if offset < 0 else 1))
        menu.addSeparator()
        menu.addAction("Delete Group…", lambda: self.delete_group(group))
        menu.addSeparator()
        menu.addAction("Add Group…", self.add_group)
        menu.addAction("Add Account…", self.add_entry)
        return menu

    def _collapse_all(self, collapsed: bool) -> None:
        for group in self.manager.groups:
            group.collapsed = collapsed
        self.manager.save()
        self.rebuild_grid()

    def eventFilter(self, obj, event):
        if (obj is self.grid_host and event.type() == QEvent.Type.MouseButtonPress
                and event.button() == Qt.MouseButton.LeftButton
                and not event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
                and len(self.selection)):
            self.clear_selection()
        return super().eventFilter(obj, event)

    def _update_header_labels(self) -> None:
        """Shorter button labels when the window is too narrow for the full ones (tooltips keep the detail)."""
        paused = self.pause_btn.isChecked()
        full = {self.pause_btn: "Resume Previews" if paused else "Pause Previews", self.add_btn: "+ Add Account"}
        short = {self.pause_btn: "Resume" if paused else "Pause", self.add_btn: "+ Add"}
        for b, text in full.items():
            b.setText(text)
        layout = self._header.layout()
        layout.invalidate()
        if layout.minimumSize().width() > self._header.width():
            for b, text in short.items():
                b.setText(text)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_header_labels()
        if self._columns and self._column_count() != self._columns:
            self.rebuild_grid()
        self.toasts.reposition()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self.rebuild_grid)

    def _update_empty_state(self) -> None:
        install = self.manager.install
        n = len(self.manager.instances)
        if n:
            self.empty_text.setText(
                f"Found {n} Prism instance{'s' if n != 1 else ''}. Add them to the dashboard, then give each "
                "one a display name like “Main” or “Farm Bot 1”.\n\n" + str(install.instances_dir)
            )
        elif not install.can_launch and not self.mock:
            self.empty_text.setText(
                f"Prism Launcher was not found. Install it from prismlauncher.org ({system.PRISM_INSTALL_HINT}), "
                "sign in to your Microsoft account in Prism, create an instance, then return here."
            )
        else:
            self.empty_text.setText(
                f"No Prism instances were found in {install.instances_dir}. Create one in Prism Launcher, "
                "or point Settings at your Prism data folder."
            )
        self.import_btn.setVisible(n > 0)

    def _update_header(self) -> None:
        total = len(self.manager.entries)
        running = self.manager.running_count()
        self.stat_label.setText(f"{total} account{'s' if total != 1 else ''}  ·  {running} running")
        played = self.manager.playtime.combined_s(e.id for e in self.manager.entries)
        self.playtime_label.setText(f"{format_duration(played)} played in total")
        self.stop_all_btn.setEnabled(running > 0)
        self.launch_all_btn.setEnabled(any(self.manager.can_launch(e.id)[0] for e in self.manager.entries))

    def refresh_entry(self, entry_id: str) -> None:
        entry = self.manager.entry(entry_id)
        if entry is None:
            return
        info = self.manager.info(entry_id)
        preview = self.previews.state(entry_id)
        pres = present(entry, self.manager.instance_for(entry), info, preview, self.previews.paused,
                       self.manager.queue.position(entry_id))
        card = self.cards.get(entry_id)
        if card is not None:
            card.update_view(pres, preview.frame, entry)
            card.set_selected(entry_id in self.selection)
        if self.stack.currentWidget() is self.detail and self.detail.entry_id == entry_id:
            self.detail.update_view(pres, preview.frame)
            self.detail.selected_tag.setVisible(entry_id in self.selection)
        self._update_header()
        if entry_id in self.selection:
            self._update_bulk_bar()

    # -------------------------------------------------------------- selection
    @property
    def selected_id(self) -> str | None:
        """The anchor of the selection (the tile clicked last), if it is still selected."""
        anchor = self.selection.anchor
        return anchor if anchor in self.selection else None

    def _apply_selection(self) -> None:
        for eid, card in self.cards.items():
            card.set_selected(eid in self.selection)
        self._update_bars()

    def select(self, entry_id: str) -> None:
        """Select just this tile."""
        self.selection.set([entry_id], entry_id)
        self._apply_selection()

    def _on_card_pressed(self, entry_id: str, modifiers) -> None:
        plain = not modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        if plain and entry_id in self.selection and len(self.selection) > 1:
            # Maybe the start of dragging the whole selection: decide on release.
            self._deferred_click = entry_id
            return
        self._deferred_click = None
        self.selection.click(entry_id, modifiers, self._display_order)
        self._apply_selection()

    def _on_card_clicked(self, entry_id: str, modifiers) -> None:
        if self._deferred_click == entry_id:  # plain click (no drag) on a selected tile
            self._deferred_click = None
            self.selection.click(entry_id, modifiers, self._display_order)
            self._apply_selection()

    def _on_card_context(self, entry_id: str) -> None:
        if entry_id not in self.selection:
            self.select(entry_id)

    def select_all_visible(self) -> None:
        if self.stack.currentWidget() is not self.detail:
            self.selection.set(self._display_order)
            self._apply_selection()

    def clear_selection(self) -> None:
        self.selection.clear()
        self._apply_selection()

    def _escape(self) -> None:
        if self.stack.currentWidget() is self.detail:
            self.show_grid()
        else:
            self.clear_selection()

    def _update_bars(self) -> None:
        self._update_bulk_bar()
        self.queue_bar.update_for(self.manager, self.manager.queue)

    def _update_bulk_bar(self) -> None:
        """The selection toolbar belongs to the grid: hidden in the expanded view, back on return."""
        ids = self.selection.ordered(self._display_order)
        in_detail = self.stack.currentWidget() is self.detail
        self.bulk_bar.update_for(self.manager, [] if in_detail else ids)

    def _on_queue_changed(self) -> None:
        for entry in self.manager.entries:
            self.refresh_entry(entry.id)
        self._update_bars()

    def _tick(self) -> None:
        self._update_header()
        if self.manager.queue.active:
            self.queue_bar.update_for(self.manager, self.manager.queue)
        if self.stack.currentWidget() is self.detail:
            self.detail.update_playtime()

    # ------------------------------------------------------------ bulk actions
    def launch_all(self) -> None:
        order = self._display_order + [e.id for e in self.manager.entries if e.id not in self._display_order]
        self.manager.launch_many(order)

    def _names(self, ids: list[str]) -> str:
        names = [self.manager.entry(i).display_name for i in ids if self.manager.entry(i)]
        shown = ", ".join(f"“{n}”" for n in names[:8])
        return shown + (f" and {len(names) - 8} more" if len(names) > 8 else "")

    def bulk_action(self, action: str) -> None:
        m = self.manager
        ids = self.selection.ordered(self._display_order)
        if action == "deselect":
            return self.clear_selection()
        if action == "launch":
            targets = [i for i in ids if m.can_launch(i)[0] and not m.queue.is_waiting(i)]
            if m.launch_many(targets):
                mode = "one after another" if m.settings.staggered_launch else "a few seconds apart"
                self.toasts.show_message("info", f"Launching {len(targets)} {mode}: {self._names(targets)}.")
            return
        running = [i for i in ids if m.info(i).state == RunState.RUNNING]
        if not running:
            return
        verb = "Stop" if action == "stop" else "Restart"
        n = len(running)
        if self._confirm(f"{verb} {n} account{'s' if n != 1 else ''}?",
                         f"Affected: {self._names(running)}.\n\nEach game is asked to close normally."
                         + ("" if action == "stop" else " It is launched again once it has closed.")):
            (m.stop_many if action == "stop" else m.restart_many)(running)

    def _cancel_queue(self) -> None:
        n = self.manager.queue.cancel_waiting()
        if n:
            self.toasts.show_message("info", f"Cancelled {n} launch{'es' if n != 1 else ''} that had not started.")

    def expand(self, entry_id: str) -> None:
        # Expanding is a button action like any other: it never changes the selection.
        self.detail.show_entry(entry_id)
        self.stack.setCurrentWidget(self.detail)
        self._update_bulk_bar()
        self.previews.set_focused(entry_id)
        self.refresh_entry(entry_id)

    def _expand_selected(self) -> None:
        if self.selected_id and self.stack.currentWidget() is not self.detail:
            self.expand(self.selected_id)

    def show_grid(self) -> None:
        if self.stack.currentWidget() is self.detail:
            self.stack.setCurrentIndex(0)
            self._update_bulk_bar()
            self.previews.set_focused(None)
            if self.selected_id:
                self.refresh_entry(self.selected_id)

    # ---------------------------------------------------------------- actions
    def handle_action(self, entry_id: str, action: str) -> None:
        m = self.manager
        entry = m.entry(entry_id)
        if entry is None:
            return
        # Buttons and menu items act on the tile without changing the selection.
        if action == "cancel_queued":
            m.queue.cancel(entry_id)
        elif action == "launch":
            m.launch(entry_id)
        elif action == "stop":
            m.stop(entry_id)
        elif action == "restart":
            m.restart(entry_id)
        elif action == "force_quit":
            if self._confirm(f"Force quit “{entry.display_name}”?",
                             "The game is killed immediately. Unsaved singleplayer progress may be lost."):
                m.force_quit(entry_id)
        elif action == "focus":
            m.bring_to_front(entry_id)
        elif action == "expand":
            self.expand(entry_id)
        elif action == "rename":
            name, ok = QInputDialog.getText(self, "Rename", "Display name:", text=entry.display_name)
            if ok and name.strip():
                entry.display_name = name.strip()
                m.update_entry(entry)
        elif action == "edit":
            dlg = EntryDialog(m, entry, self)
            if dlg.exec():
                m.update_entry(entry)
                self.rebuild_grid()
        elif action.startswith("group:"):
            m.move_entry(entry_id, action.removeprefix("group:"))
        elif action == "group_new":
            name = self.add_group()
            if name:
                m.move_entry(entry_id, name)
        elif action == "toggle_favorite":
            entry.favorite = not entry.favorite
            m.update_entry(entry)
            self.rebuild_grid()
        elif action == "open_folder":
            path = m.game_dir(entry_id)
            if path:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        elif action == "open_crash":
            report = m.info(entry_id).crash_report
            if report:
                QDesktopServices.openUrl(QUrl.fromLocalFile(report))
        elif action == "show_in_prism":
            m.show_in_prism(entry_id)
        elif action == "remove":
            if m.info(entry_id).state.is_active:
                self.toasts.show_message("warning", "Stop the instance before removing it from the dashboard.")
            elif self._confirm(f"Remove “{entry.display_name}” from the dashboard?",
                               "This only removes the dashboard entry. The Prism instance and its worlds are not touched."):
                self.show_grid()
                m.remove_entry(entry_id)

    def _confirm(self, title: str, text: str) -> bool:
        box = QMessageBox(QMessageBox.Icon.Warning, title, title, QMessageBox.StandardButton.Cancel, self)
        box.setInformativeText(text)
        ok = box.addButton("Continue", QMessageBox.ButtonRole.AcceptRole)
        box.exec()
        return box.clickedButton() is ok

    def stop_all(self) -> None:
        running = [e for e in self.manager.entries if self.manager.info(e.id).state == RunState.RUNNING]
        if running and self._confirm(f"Stop {len(running)} running instance{'s' if len(running) != 1 else ''}?",
                                     "Each game is asked to close normally."):
            self.manager.stop_all()

    def add_entry(self) -> None:
        dlg = EntryDialog(self.manager, None, self)
        if dlg.exec() and dlg.result_entry:
            self.manager.add_entry(dlg.result_entry)
            self.select(dlg.result_entry.id)
            self.refresh_entry(dlg.result_entry.id)

    def import_all(self) -> None:
        n = self.manager.import_all_instances()
        self.toasts.show_message("info", f"Added {n} instance{'s' if n != 1 else ''}. Rename them from each tile’s ⋯ menu.")

    def open_settings(self) -> None:
        dlg = SettingsDialog(self.manager, self, notifier=self.notifier)
        if dlg.exec():
            self.manager.save()
            self.manager.refresh_prism(relocate=True)
            self.previews.apply_backend_setting()
            self.previews.refresh_targets()
            self.rebuild_grid()
            self._banner_key = None
            self.refresh_banners()

    def _toggle_pause(self, paused: bool) -> None:
        self._update_header_labels()
        self.previews.set_paused(paused)
        for entry in self.manager.entries:
            self.refresh_entry(entry.id)

    # ---------------------------------------------------------------- banners
    def refresh_banners(self) -> None:
        install = self.manager.install
        needs_capture = system.needs_privacy_permissions() and not self.mock and (
            not permissions.screen_capture_allowed() or self.previews.permission_problem)
        key = (self.mock, install.can_launch, install.data_dir_exists, needs_capture, tuple(install.warnings))
        if key == getattr(self, "_banner_key", None):
            return
        self._banner_key = key
        while self.banner_box.count():
            w = self.banner_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        if self.mock:
            b = Banner("info")
            b.set_text("<b>Mock mode.</b> Instances, game processes and previews are simulated. "
                       "Process tracking, log parsing and crash detection run on real (fake-client) processes. "
                       "“Mock Crashy” crashes ~20 s after launch to demonstrate crash detection.")
            self.banner_box.addWidget(b)
            return
        if not install.can_launch:
            b = Banner("error")
            b.set_text("<b>Prism Launcher was not found.</b> Install it from "
                       "<a href='https://prismlauncher.org'>prismlauncher.org</a>, sign in to your Microsoft "
                       f"account there, and {system.PRISM_INSTALL_HINT} — or choose its location in Settings.")
            b.add_action("Settings…", self.open_settings)
            self.banner_box.addWidget(b)
        elif not install.data_dir_exists:
            b = Banner("warning")
            b.set_text(f"<b>Prism data folder not found</b> at {install.instances_dir}. "
                       "Open Prism Launcher once, or set the data folder in Settings.")
            b.add_action("Settings…", self.open_settings)
            self.banner_box.addWidget(b)
        if needs_capture:
            b = Banner("warning")
            b.set_text("<b>Live previews need Screen Recording permission.</b> Turn it on for "
                       f"{permissions.permission_holder()} in System Settings › Privacy &amp; "
                       "Security › Screen &amp; System Audio Recording, then quit and reopen this app.")
            b.add_action("Request Access", lambda: permissions.request_screen_capture())
            b.add_action("Open Settings", lambda: QDesktopServices.openUrl(QUrl(permissions.SCREEN_RECORDING_SETTINGS_URL)))
            self.banner_box.addWidget(b)
        for warning in install.warnings:
            b = Banner("info")
            b.set_text(warning)
            self.banner_box.addWidget(b)

    def closeEvent(self, event):
        running = self.manager.running_count()
        if running:
            log.info("Closing dashboard; %d game(s) keep running", running)
        super().closeEvent(event)
