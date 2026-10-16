"""Dashboard sections: one per user-named group, plus the ungrouped section.

Cards are dragged between (and within) sections with Qt drag and drop. The
drag carries only the entry id (``ENTRY_MIME``); the section that receives the
drop works out the insertion point from the cursor position and emits
``dropped``, and the main window asks the manager to move the entry.
"""

from __future__ import annotations

from PySide6.QtCore import QMimeData, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from mcmanager.ui import theme
from mcmanager.ui.widgets import button, repolish

ENTRY_MIME = "application/x-mcmanager-entry"
SECTION_PAD = 8  # inner padding, leaves room for the drop highlight
CARD_SPACING = 16


def entry_mime(entry_ids: str | list[str]) -> QMimeData:
    """Drag payload: one or more entry ids (several when a multi-selection is dragged)."""
    ids = [entry_ids] if isinstance(entry_ids, str) else list(entry_ids)
    mime = QMimeData()
    mime.setData(ENTRY_MIME, "\n".join(ids).encode())
    return mime


def entry_ids_from(mime: QMimeData) -> list[str]:
    if not mime.hasFormat(ENTRY_MIME):
        return []
    return [i for i in bytes(mime.data(ENTRY_MIME).data()).decode().split("\n") if i]


def entry_id_from(mime: QMimeData) -> str | None:
    ids = entry_ids_from(mime)
    return ids[0] if ids else None


def insertion_index(rects: list[QRect], pos: QPoint) -> int:
    """Index (in reading order) at which a card dropped at ``pos`` should be inserted."""
    for i, r in enumerate(rects):
        if pos.y() < r.top() - CARD_SPACING // 2:
            return i  # above this row
        if pos.y() <= r.bottom() + CARD_SPACING // 2 and pos.x() < r.center().x():
            return i  # in this row, left of this card's middle
    return len(rects)


class GroupSection(QFrame):
    """A titled, collapsible block of cards that accepts card drops.

    ``group`` is the group name, or "" for the ungrouped section.
    """

    dropped = Signal(list, str, str)  # entry_ids, group, before_id ("" = end of group)
    collapse_toggled = Signal(str, bool)
    menu_requested = Signal(str, QPoint)  # group, global position
    rename_requested = Signal(str)

    def __init__(self, group: str, title: str, collapsed: bool, show_header: bool, pinned: bool = False):
        super().__init__()
        self.group = group
        self.collapsed = collapsed
        self.cards: list[QWidget] = []
        self._drop_index: int | None = None
        self.setObjectName("GroupSection")
        self.setProperty("dropTarget", False)
        self.setAcceptDrops(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda p: self.menu_requested.emit(self.group, self.mapToGlobal(p)))

        root = QVBoxLayout(self)
        root.setContentsMargins(SECTION_PAD, SECTION_PAD, SECTION_PAD, SECTION_PAD)
        root.setSpacing(10)

        self.header = QWidget(self)  # parented now: a parentless widget shown early is a window
        head = QHBoxLayout(self.header)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        if group:
            self.arrow = button("▶" if collapsed else "▼", "ghost", "Collapse or expand this group")
            self.arrow.setObjectName("GroupArrow")
            self.arrow.setFixedWidth(28)
            self.arrow.clicked.connect(lambda: self.collapse_toggled.emit(self.group, not self.collapsed))
            head.addWidget(self.arrow)
        else:
            head.addSpacing(4)
        self.title = QLabel(title)
        self.title.setObjectName("GroupTitle")
        if group:
            self.title.setToolTip("Double-click to rename")
        head.addWidget(self.title)
        self.count = QLabel()
        self.count.setObjectName("GroupCount")
        head.addWidget(self.count)
        self.pin = QLabel("📌 Pinned", self.header)
        self.pin.setObjectName("GroupPinned")
        self.pin.setToolTip("Pinned groups stay at the top. Right-click the group to unpin it.")
        self.pin.setVisible(pinned)
        head.addWidget(self.pin)
        head.addStretch(1)
        if group:
            more = button("⋯", "ghost", "Group actions")
            more.setFixedWidth(32)
            more.clicked.connect(lambda: self.menu_requested.emit(self.group, more.mapToGlobal(more.rect().bottomLeft())))
            head.addWidget(more)
        self.header.setVisible(show_header)
        root.addWidget(self.header)

        self.flow = QWidget(self)
        self.grid = QGridLayout(self.flow)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(CARD_SPACING)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        root.addWidget(self.flow)

        self.placeholder = QLabel("Drag accounts here" if group else "Drag an account here to take it out of its group", self)
        self.placeholder.setObjectName("GroupEmpty")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.placeholder)
        self._update_visibility()

    def set_cards(self, cards: list[QWidget], columns: int, total: int) -> None:
        """Lay out ``cards`` (the visible ones); ``total`` is the group's size for the count badge."""
        self.cards = [] if self.collapsed else list(cards)
        for i, card in enumerate(self.cards):
            card.setParent(self.flow)
            self.grid.addWidget(card, i // columns, i % columns)
            card.show()
        self.count.setText(str(total))
        self._update_visibility()

    def _update_visibility(self) -> None:
        self.flow.setVisible(bool(self.cards))
        self.placeholder.setVisible(not self.collapsed and not self.cards)

    def mouseDoubleClickEvent(self, event):
        if self.group and self.header.geometry().contains(event.position().toPoint()):
            self.rename_requested.emit(self.group)
        else:
            super().mouseDoubleClickEvent(event)

    # ------------------------------------------------------------ drag & drop
    def _card_rects(self) -> list[QRect]:
        return [QRect(c.mapTo(self, QPoint(0, 0)), c.size()) for c in self.cards]

    def _set_drop_target(self, on: bool, index: int | None = None) -> None:
        self._drop_index = index if on else None
        if self.property("dropTarget") != on:
            self.setProperty("dropTarget", on)
            repolish(self)
        self.update()

    def dragEnterEvent(self, event):
        if entry_id_from(event.mimeData()):
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
            self._set_drop_target(True, insertion_index(self._card_rects(), event.position().toPoint()))
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if entry_id_from(event.mimeData()):
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
            self._set_drop_target(True, insertion_index(self._card_rects(), event.position().toPoint()))

    def dragLeaveEvent(self, _event):
        self._set_drop_target(False)

    def dropEvent(self, event):
        entry_ids = entry_ids_from(event.mimeData())
        index = insertion_index(self._card_rects(), event.position().toPoint())
        self._set_drop_target(False)
        if not entry_ids:
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()
        before = self.cards[index].entry_id if index < len(self.cards) else ""
        self.dropped.emit(entry_ids, self.group, before)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._drop_index is None or not self.cards:
            return
        rects = self._card_rects()
        if self._drop_index < len(rects):
            r = rects[self._drop_index]
            x = r.left() - CARD_SPACING // 2
        else:
            r = rects[-1]
            x = r.right() + CARD_SPACING // 2
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.ACCENT))
        p.drawRoundedRect(QRect(x - 2, r.top(), 4, r.height()), 2, 2)
