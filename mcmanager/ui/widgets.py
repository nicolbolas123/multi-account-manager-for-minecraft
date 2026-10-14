"""Small reusable widgets."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from mcmanager.ui import theme


def button(text: str, kind: str | None = None, tooltip: str | None = None) -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if tooltip:
        b.setToolTip(tooltip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def format_bytes(n: int | None) -> str:
    if n is None:
        return "—"
    if n >= 1 << 30:
        return f"{n / (1 << 30):.1f} GB"
    return f"{n / (1 << 20):.0f} MB"


def repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class StatusDot(QWidget):
    def __init__(self, size: int = 10):
        super().__init__()
        self._color = QColor(theme.TEXT_FAINT)
        self.setFixedSize(size, size)

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._color)
        p.drawEllipse(self.rect().adjusted(1, 1, -1, -1))


class PreviewView(QWidget):
    """Draws a captured frame letterboxed into a rounded 16:9-ish area with optional overlay text."""

    clicked = Signal()
    double_clicked = Signal()

    def __init__(self, radius: int = 9, min_height: int = 120):
        super().__init__()
        self._image: QImage | None = None
        self._title = ""
        self._subtitle = ""
        self._dim = False
        self._accent = QColor(theme.ACCENT)
        self._radius = radius
        self.setMinimumHeight(min_height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, w: int) -> int:
        return max(self.minimumHeight(), int(w * 9 / 16))

    def sizeHint(self) -> QSize:
        return QSize(320, 180)

    def set_frame(self, image: QImage | None, dim: bool = False) -> None:
        self._image, self._dim = image, dim
        self.update()

    def set_overlay(self, title: str = "", subtitle: str = "") -> None:
        if (title, subtitle) != (self._title, self._subtitle):
            self._title, self._subtitle = title, subtitle
            self.update()

    def set_accent(self, color: str) -> None:
        self._accent = QColor(color)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)
        p.setClipPath(path)
        p.fillRect(rect, QColor("#0a0c10"))

        if self._image is not None and not self._image.isNull():
            img_size = self._image.size()
            scaled = img_size.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
            target = QRectF(
                (self.width() - scaled.width()) / 2, (self.height() - scaled.height()) / 2,
                scaled.width(), scaled.height(),
            )
            p.drawImage(target, self._image)
            if self._dim or self._title:
                p.fillRect(rect, QColor(0, 0, 0, 150))
        else:
            # Subtle placeholder pattern.
            p.setPen(QPen(QColor(255, 255, 255, 10), 1))
            step = 18
            for x in range(-self.height(), self.width(), step):
                p.drawLine(x, self.height(), x + self.height(), 0)

        if self._title:
            p.setPen(QColor(theme.TEXT))
            f = QFont(self.font())
            f.setPixelSize(max(12, min(18, self.height() // 9)))
            f.setBold(True)
            p.setFont(f)
            title_rect = rect.adjusted(12, 0, -12, -(16 if self._subtitle else 0))
            p.drawText(title_rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self._title)
            if self._subtitle:
                f.setBold(False)
                f.setPixelSize(max(10, min(13, self.height() // 13)))
                p.setFont(f)
                p.setPen(QColor(theme.TEXT_DIM))
                sub_rect = rect.adjusted(12, self.height() / 2 + 6, -12, 0)
                p.drawText(sub_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                           self._subtitle)


class Banner(QFrame):
    """Inline notice with optional action buttons."""

    def __init__(self, kind: str = "info"):
        super().__init__()
        self.setObjectName("Banner")
        self.setProperty("kind", kind)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 10, 10)
        layout.setSpacing(10)
        self.label = QLabel()
        self.label.setObjectName("BannerText")
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.label.setOpenExternalLinks(True)
        layout.addWidget(self.label, 1)
        self._buttons = QHBoxLayout()
        self._buttons.setSpacing(6)
        layout.addLayout(self._buttons)

    def set_text(self, html: str) -> None:
        self.label.setText(html)

    def add_action(self, text: str, callback, kind: str | None = None) -> QPushButton:
        b = button(text, kind)
        b.clicked.connect(callback)
        self._buttons.addWidget(b)
        return b


class ToastArea(QWidget):
    """Stack of transient notifications in the bottom-right corner of its parent."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)
        self._layout.addStretch(1)
        self.setFixedWidth(380)

    def reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self.adjustSize()
        h = min(self.sizeHint().height(), parent.height() - 40)
        self.setGeometry(parent.width() - self.width() - 18, parent.height() - h - 18, self.width(), h)
        self.raise_()

    def show_message(self, kind: str, message: str, timeout_ms: int = 6000) -> None:
        toast = QFrame()
        toast.setObjectName("Toast")
        toast.setProperty("kind", kind)
        row = QHBoxLayout(toast)
        row.setContentsMargins(12, 10, 6, 10)
        dot = StatusDot(9)
        dot.set_color({"error": theme.DANGER, "warning": theme.WARNING}.get(kind, theme.ACCENT))
        row.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
        label = QLabel(message)
        label.setWordWrap(True)
        row.addWidget(label, 1)
        close = button("✕", "ghost")
        close.setFixedWidth(28)
        row.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)

        def remove():
            if toast.parent() is not None:
                toast.setParent(None)
                toast.deleteLater()
                self.reposition()

        close.clicked.connect(remove)
        while self._layout.count() > 5:  # keep the stack short
            old = self._layout.itemAt(1).widget()
            old.setParent(None)
            old.deleteLater()
        self._layout.addWidget(toast)
        self.show()
        self.reposition()
        QTimer.singleShot(timeout_ms if kind == "info" else timeout_ms * 2, remove)


def toggle(text: str, checked: bool, tooltip: str | None = None) -> QCheckBox:
    """A setting switch: highlighted in blue with a check mark while on, plain while off.

    Its look comes only from the ``checked`` state (stylesheet ``#Toggle``), so it can never
    disagree with the value that is saved.
    """
    box = QCheckBox(text)
    box.setObjectName("Toggle")
    box.setChecked(checked)
    box.setCursor(Qt.CursorShape.PointingHandCursor)
    if tooltip:
        box.setToolTip(tooltip)
    return box


def section_label(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setObjectName("SectionLabel")
    return label


class ElidedLabel(QLabel):
    """A single-line label that shortens its text with "…" instead of overlapping its neighbours."""

    def __init__(self, text: str = "", min_width: int = 80):
        super().__init__(text)
        self._min_width = min_width
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    def minimumSizeHint(self) -> QSize:
        return QSize(self._min_width, super().minimumSizeHint().height())

    def paintEvent(self, _event):
        p = QPainter(self)
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width())
        p.setPen(self.palette().color(self.foregroundRole()))
        p.drawText(self.rect(), int(self.alignment() | Qt.AlignmentFlag.AlignVCenter), text)
