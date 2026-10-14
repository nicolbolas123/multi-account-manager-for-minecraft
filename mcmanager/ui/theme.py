"""Dark theme: colour tokens and the application stylesheet."""

from __future__ import annotations

from PySide6.QtCore import QDir
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from mcmanager.bundle import asset_dir
from mcmanager.runtime.models import RunState

BG = "#0f1115"
SURFACE = "#171a21"
SURFACE_2 = "#1e222b"
SURFACE_3 = "#272c37"
BORDER = "#2c313c"
TEXT = "#e6e8ee"
TEXT_DIM = "#9aa1b1"
TEXT_FAINT = "#6b7282"
ACCENT = "#5b8cff"
ACCENT_HOVER = "#7aa2ff"
DANGER = "#f05d5d"
WARNING = "#f5b74a"
SUCCESS = "#4ade80"

STATE_COLORS = {
    RunState.STOPPED: "#6b7282",
    RunState.LAUNCHING: "#f5b74a",
    RunState.RUNNING: "#4ade80",
    RunState.STOPPING: "#fb923c",
    RunState.EXITED: "#8b93a5",
    RunState.CRASHED: "#f05d5d",
    RunState.FAILED: "#f05d5d",
}

STYLESHEET = f"""
QWidget {{ color: {TEXT}; font-size: 13px; }}
QMainWindow, #Root {{ background: {BG}; }}
QScrollArea, #GridHost {{ background: {BG}; border: none; }}
QToolTip {{ background: {SURFACE_3}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px 6px; }}

#Header {{ background: {SURFACE}; border-bottom: 1px solid {BORDER}; }}
#AppTitle {{ font-size: 18px; font-weight: 700; }}
#HeaderStat {{ color: {TEXT_DIM}; font-size: 12px; }}
#MockBadge {{ background: #3b2a06; color: {WARNING}; border: 1px solid #6b4c0c; border-radius: 9px;
              padding: 2px 8px; font-size: 11px; font-weight: 700; }}

QPushButton {{ background: {SURFACE_3}; border: 1px solid {BORDER}; border-radius: 7px;
               padding: 6px 12px; color: {TEXT}; }}
QPushButton:hover {{ background: #303644; border-color: #3a4150; }}
QPushButton:pressed {{ background: #232833; }}
QPushButton:disabled {{ color: {TEXT_FAINT}; background: {SURFACE_2}; border-color: {SURFACE_3}; }}
QPushButton[kind="primary"] {{ background: {ACCENT}; border-color: {ACCENT}; color: white; font-weight: 600; }}
QPushButton[kind="primary"]:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton[kind="primary"]:disabled {{ background: #2c3a5e; border-color: #2c3a5e; color: #8792b0; }}
QPushButton[kind="danger"] {{ background: #3a1d22; border-color: #5a2a31; color: #ffb4b4; }}
QPushButton[kind="danger"]:hover {{ background: #4a232a; }}
QPushButton[kind="danger"]:disabled {{ background: {SURFACE_2}; border-color: {SURFACE_3}; color: {TEXT_FAINT}; }}
QPushButton[kind="ghost"] {{ background: transparent; border-color: transparent; color: {TEXT_DIM}; padding: 4px 8px; }}
QPushButton[kind="ghost"]:hover {{ background: {SURFACE_3}; color: {TEXT}; }}
QPushButton::menu-indicator {{ image: none; width: 0; }}

#Card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
#Card:hover {{ border-color: #3a4150; }}
#Card[selected="true"] {{ border: 2px solid {ACCENT}; background: #1a2135; }}
#CardName {{ font-size: 15px; font-weight: 650; }}
#CardSub {{ color: {TEXT_DIM}; font-size: 12px; }}
#CardMeta {{ color: {TEXT_FAINT}; font-size: 11px; }}

#GroupSection {{ border: 1px dashed transparent; border-radius: 14px; }}
#GroupSection[dropTarget="true"] {{ border-color: {ACCENT}; background: rgba(91, 140, 255, 0.06); }}
#GroupArrow {{ font-size: 10px; color: {TEXT_DIM}; }}
#GroupPinned {{ color: {WARNING}; font-size: 11px; font-weight: 600; }}
#GroupTitle {{ font-size: 15px; font-weight: 700; }}
#GroupCount {{ color: {TEXT_DIM}; background: {SURFACE_3}; border-radius: 8px; padding: 1px 7px; font-size: 11px; }}
#GroupEmpty {{ color: {TEXT_FAINT}; border: 1px dashed {BORDER}; border-radius: 10px; padding: 22px; }}
#Panel {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
#DetailTitle {{ font-size: 22px; font-weight: 700; }}
#FieldLabel {{ color: {TEXT_FAINT}; font-size: 11px; text-transform: uppercase; }}
#FieldValue {{ color: {TEXT}; font-size: 13px; }}

#Banner {{ border-radius: 10px; }}
#Banner[kind="warning"] {{ background: #2e2410; border: 1px solid #5c4614; }}
#Banner[kind="error"] {{ background: #2e1416; border: 1px solid #5c2328; }}
#Banner[kind="info"] {{ background: #142033; border: 1px solid #23395c; }}
#BannerText {{ color: {TEXT}; }}

#Toast {{ background: {SURFACE_3}; border: 1px solid {BORDER}; border-radius: 9px; }}
#Toast[kind="error"] {{ border-color: #6b2a31; }}
#Toast[kind="warning"] {{ border-color: #6b4c0c; }}

#EmptyTitle {{ font-size: 20px; font-weight: 700; }}
#EmptyText {{ color: {TEXT_DIM}; font-size: 13px; }}

QMenu {{ background: {SURFACE_2}; border: 1px solid {BORDER}; padding: 4px; border-radius: 8px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 5px; }}
QMenu::item:selected {{ background: {SURFACE_3}; }}
QMenu::item:disabled {{ color: {TEXT_FAINT}; }}
#FeedbackText {{ color: {TEXT_DIM}; padding: 6px 18px; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}

QDialog {{ background: {SURFACE}; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background: {SURFACE_2}; border: 1px solid {BORDER};
    border-radius: 6px; padding: 5px 8px; selection-background-color: {ACCENT}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {ACCENT}; }}
QComboBox QAbstractItemView {{ background: {SURFACE_2}; border: 1px solid {BORDER}; selection-background-color: {SURFACE_3}; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid #485063; background: {SURFACE_2}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url(mcm:check.svg); }}
QCheckBox::indicator:disabled {{ background: {SURFACE}; border-color: {BORDER}; }}
QCheckBox::indicator:checked:disabled {{ background: #2c3a5e; border-color: #2c3a5e; image: url(mcm:check-dim.svg); }}
/* Setting toggles: the whole row turns blue (with a check mark) when the setting is on. */
QCheckBox#Toggle {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 7px 10px; spacing: 10px; color: {TEXT_DIM}; }}
QCheckBox#Toggle:hover {{ border-color: #3a4150; color: {TEXT}; }}
QCheckBox#Toggle:checked {{ background: rgba(91, 140, 255, 0.18); border-color: {ACCENT}; color: {TEXT}; font-weight: 600; }}
QCheckBox#Toggle:disabled {{ background: {SURFACE}; border-color: {BORDER}; color: {TEXT_FAINT}; font-weight: 400; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 8px; top: -1px; }}
QTabBar::tab {{ background: transparent; color: {TEXT_DIM}; padding: 7px 14px; border: 1px solid transparent;
    border-top-left-radius: 7px; border-top-right-radius: 7px; }}
QTabBar::tab:selected {{ background: {SURFACE_2}; color: {TEXT}; border-color: {BORDER}; border-bottom-color: {SURFACE_2}; }}
QTabBar::tab:hover:!selected {{ color: {TEXT}; }}
#SectionLabel {{ color: {TEXT_DIM}; font-size: 11px; font-weight: 700; letter-spacing: 0.5px; }}
#ActionBar {{ background: #16213d; border: 1px solid #2b4387; border-radius: 10px; }}
#ActionBarTitle {{ font-weight: 700; color: #cddbff; }}
#ActionBarText {{ color: {TEXT_DIM}; }}
#SelectBadge {{ background: {ACCENT}; color: white; border-radius: 9px; font-size: 11px; font-weight: 800; }}
QGroupBox {{ border: 1px solid {BORDER}; border-radius: 8px; margin-top: 14px; padding-top: 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {TEXT_DIM}; }}
QSlider::groove:horizontal {{ height: 4px; background: {SURFACE_3}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {ACCENT}; width: 14px; margin: -5px 0; border-radius: 7px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {SURFACE_3}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
"""

def apply_theme(app: QApplication) -> None:
    QDir.addSearchPath("mcm", str(asset_dir()))  # url(mcm:check.svg) in the QSS (source tree or .app bundle)
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(BG))
    pal.setColor(QPalette.ColorRole.Base, QColor(SURFACE_2))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(SURFACE))
    pal.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.Button, QColor(SURFACE_3))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("white"))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(TEXT_FAINT))
    app.setPalette(pal)
    app.setStyleSheet(STYLESHEET)
