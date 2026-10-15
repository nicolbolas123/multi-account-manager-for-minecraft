"""Add/edit entry dialog and settings dialog."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from mcmanager import APP_NAME, system
from mcmanager.config import LABEL_COLORS, DashboardEntry
from mcmanager.config.settings import LAUNCH_DELAY_RANGE, NOTIFICATION_CATEGORIES
from mcmanager.runtime.manager import InstanceManager
from mcmanager.ui.widgets import toggle
from mcmanager.windows import permissions
from mcmanager.windows.capture import ScreenCaptureKitBackend


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("CardMeta")
    label.setWordWrap(True)
    return label


class EntryDialog(QDialog):
    """Create or edit a dashboard entry. Changes are applied to ``self.result_entry`` on accept."""

    def __init__(self, manager: InstanceManager, entry: DashboardEntry | None = None, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.entry = entry
        self.result_entry: DashboardEntry | None = None
        self._name_touched = entry is not None
        self.setWindowTitle("Edit Account" if entry else "Add Account")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        form = QFormLayout()
        form.setSpacing(10)

        self.name = QLineEdit(entry.display_name if entry else "")
        self.name.setPlaceholderText("e.g. Main Account, Farm Bot 1, Builder")
        self.name.textEdited.connect(lambda _t: setattr(self, "_name_touched", True))
        form.addRow("Display name", self.name)

        self.instance = QComboBox()
        for inst in sorted(manager.instances.values(), key=lambda i: i.name.lower()):
            users = [e.display_name for e in manager.entries
                     if e.prism_instance_id == inst.id and (entry is None or e.id != entry.id)]
            label = inst.name + (f"  ({inst.summary})" if inst.summary else "")
            if users:
                label += f"  — also used by {', '.join(users)}"
            self.instance.addItem(label, inst.id)
        if entry and entry.prism_instance_id not in manager.instances:
            self.instance.addItem(f"{entry.prism_instance_id} (missing in Prism)", entry.prism_instance_id)
        if entry:
            self.instance.setCurrentIndex(max(0, self.instance.findData(entry.prism_instance_id)))
        else:
            unused = manager.unused_instances()
            if unused:
                self.instance.setCurrentIndex(max(0, self.instance.findData(unused[0].id)))
        self.instance.currentIndexChanged.connect(self._instance_changed)
        form.addRow("Prism instance", self.instance)

        self.account = QComboBox()
        self.account.addItem("Prism's active account (default)", None)
        for acc in manager.accounts:
            self.account.addItem(acc.profile_name + ("  (active)" if acc.active else ""), acc.profile_name)
        if entry and entry.account_profile:
            idx = self.account.findData(entry.account_profile)
            if idx < 0:
                self.account.addItem(f"{entry.account_profile} (not found in Prism)", entry.account_profile)
                idx = self.account.count() - 1
            self.account.setCurrentIndex(idx)
        form.addRow("Account", self.account)

        color_row = QHBoxLayout()
        color_row.setSpacing(6)
        self.colors = QButtonGroup(self)
        current = entry.color if entry else manager.next_color()
        for color in LABEL_COLORS:
            b = QPushButton()
            b.setCheckable(True)
            b.setFixedSize(24, 24)
            b.setStyleSheet(
                f"QPushButton {{ background: {color}; border-radius: 12px; border: 2px solid transparent; }}"
                f"QPushButton:checked {{ border: 2px solid white; }}"
            )
            b.setProperty("color", color)
            b.setChecked(color == current)
            self.colors.addButton(b)
            color_row.addWidget(b)
        color_row.addStretch(1)
        form.addRow("Color label", color_row)

        self.group = QComboBox()
        self.group.setEditable(True)
        self.group.addItems([""] + [g.name for g in manager.groups])
        self.group.setCurrentText(entry.group if entry else "")
        self.group.lineEdit().setPlaceholderText("None — pick a group or type a new name")
        form.addRow("Group", self.group)
        self.favorite = toggle("Favorite", bool(entry and entry.favorite))
        form.addRow("", self.favorite)
        layout.addLayout(form)

        if not manager.instances:
            layout.addWidget(_hint(
                f"No Prism instances were found in {manager.install.instances_dir}. Create an instance in "
                "Prism Launcher (or set the data folder in Settings) first."
            ))
        layout.addWidget(_hint(
            "Display names, colours and groups are stored only by this app; they do not change Prism or "
            "Minecraft. Prism runs each instance once at a time, so to run several accounts simultaneously, "
            "create a copy of the instance in Prism (right-click › Copy) for each account."
        ))

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._instance_changed()

    def _instance_changed(self) -> None:
        inst = self.manager.instances.get(self.instance.currentData())
        if inst and not self._name_touched:
            self.name.setText(inst.name)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(self.instance.count() > 0)

    def _accept(self) -> None:
        name = self.name.text().strip()
        if not name:
            self.name.setFocus()
            self.name.setPlaceholderText("A display name is required")
            return
        color_btn = self.colors.checkedButton()
        color = color_btn.property("color") if color_btn else LABEL_COLORS[0]
        entry = self.entry or DashboardEntry(display_name=name, prism_instance_id=self.instance.currentData())
        entry.display_name = name
        entry.prism_instance_id = self.instance.currentData()
        entry.account_profile = self.account.currentData()
        entry.color = color
        entry.group = self.group.currentText().strip()
        entry.favorite = self.favorite.isChecked()
        self.result_entry = entry
        self.accept()


class SettingsDialog(QDialog):
    """All app settings, in tabs. Nothing is applied until Save."""

    def __init__(self, manager: InstanceManager, parent=None, notifier=None):
        super().__init__(parent)
        self.manager = manager
        self.notifier = notifier
        self._test_result: str | None = None
        s = manager.settings
        self.setWindowTitle("Settings")
        self.setMinimumWidth(640)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(False)
        layout.addWidget(self.tabs)
        self.tabs.addTab(self._general_tab(s), "General")
        self.tabs.addTab(self._launching_tab(s), "Launching")
        self.tabs.addTab(self._notifications_tab(s), "Notifications")
        self.tabs.addTab(self._previews_tab(s), "Previews")
        self.tabs.addTab(self._permissions_tab(), "Permissions")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _page() -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)
        return page, lay

    def _general_tab(self, s) -> QWidget:
        page, lay = self._page()
        prism = QGroupBox("Prism Launcher")
        pf = QFormLayout(prism)
        install = self.manager.install
        detected = str(install.app_bundle or install.executable or "Not found")
        version = f" (version {install.version})" if install.version else ""
        status = QLabel(f"{detected}{version}")
        status.setWordWrap(True)
        status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        pf.addRow("Using", status)
        self.app_path = QLineEdit(s.prism_app_path or "")
        self.app_path.setPlaceholderText("Auto-detect (recommended)")
        pf.addRow("App override", self._with_browse(self.app_path, self._browse_app))
        self.data_dir = QLineEdit(s.prism_data_dir or "")
        self.data_dir.setPlaceholderText(f"Default: {install.data_dir if not install.data_dir_overridden else ''}")
        pf.addRow("Data folder", self._with_browse(self.data_dir, self._browse_data))
        for warning in install.warnings:
            pf.addRow("", _hint("⚠ " + warning))
        lay.addWidget(prism)

        dash = QGroupBox("Dashboard")
        df = QVBoxLayout(dash)
        self.favorites_first = toggle("Show favorites first in each group", s.favorites_first)
        df.addWidget(self.favorites_first)
        df.addWidget(_hint("When off, favorites keep their ★ but stay where you dragged them."))
        lay.addWidget(dash)
        lay.addStretch(1)
        return page

    def _launching_tab(self, s) -> QWidget:
        page, lay = self._page()
        box = QGroupBox("Launching several accounts")
        bl = QVBoxLayout(box)
        self.staggered = toggle("Staggered Launch", s.staggered_launch,
                                "Launch multi-account requests one after another instead of all at once")
        bl.addWidget(self.staggered)
        bl.addWidget(_hint(
            "Applies to Launch All, Launch Group, Launch Selected and restarting several accounts. Each "
            "launch waits for the delay below and for the previous game to start (or fail), to avoid a "
            "sudden CPU, memory and disk spike. When off, launches start 2.5 s apart without waiting."))
        row = QHBoxLayout()
        row.setSpacing(10)
        self.delay_label = QLabel("Launch delay")
        row.addWidget(self.delay_label)
        lo, hi = LAUNCH_DELAY_RANGE
        self.delay = QSpinBox()
        self.delay.setRange(lo, hi)  # the spin box itself rejects negative or out-of-range input
        self.delay.setSuffix(" seconds")
        self.delay.setSingleStep(5)
        self.delay.setValue(s.launch_delay_s)
        self.delay.setToolTip(f"Minimum time between consecutive launch attempts ({lo}–{hi} s)")
        row.addWidget(self.delay)
        self.delay_note = QLabel()
        self.delay_note.setObjectName("CardMeta")
        row.addWidget(self.delay_note, 1)
        bl.addLayout(row)
        self.staggered.toggled.connect(self._update_delay_state)
        self._update_delay_state()
        lay.addWidget(box)

        timeouts = QGroupBox("Launch timeout")
        tf = QFormLayout(timeouts)
        self.timeout = QSpinBox()
        self.timeout.setRange(30, 1800)
        self.timeout.setSuffix(" s")
        self.timeout.setValue(s.launch_timeout_s)
        tf.addRow("Give up after", self.timeout)
        tf.addRow("", _hint("A launch is marked as failed if Minecraft's process doesn't appear within this time."))
        lay.addWidget(timeouts)
        lay.addStretch(1)
        return page

    def _update_delay_state(self) -> None:
        on = self.staggered.isChecked()
        self.delay.setEnabled(on)
        self.delay_label.setEnabled(on)
        self.delay_note.setText("" if on else "Only applies when Staggered Launch is on")

    def _notifications_tab(self, s) -> QWidget:
        from mcmanager.notifications import AVAILABLE_CATEGORIES, UNAVAILABLE_REASON

        n = s.notifications
        page, lay = self._page()
        self.notify_master = toggle("Enable Notifications", n.enabled,
                                    "Master switch for optional notifications")
        lay.addWidget(self.notify_master)
        lay.addWidget(_hint("Errors are always shown in the dashboard and written to the log, even when "
                            "notifications are off."))
        self.notify_box = QGroupBox("Notify me when…")
        nl = QVBoxLayout(self.notify_box)
        nl.setSpacing(6)
        self.notify_toggles: dict[str, QCheckBox] = {}
        for key, (label, description) in NOTIFICATION_CATEGORIES.items():
            available = key in AVAILABLE_CATEGORIES
            box = toggle(label, getattr(n, key) and available, description)
            box.setProperty("available", available)
            self.notify_toggles[key] = box
            nl.addWidget(box)
            if not available:
                nl.addWidget(_hint(UNAVAILABLE_REASON.get(key, "Not available.")))
        lay.addWidget(self.notify_box)

        test_row = QHBoxLayout()
        self.test_btn = QPushButton("Send Test Notification")
        self.test_btn.clicked.connect(self._send_test)
        test_row.addWidget(self.test_btn)
        self.backend_note = _hint("")
        test_row.addWidget(self.backend_note, 1)
        lay.addLayout(test_row)
        self.permission_note = _hint("")
        lay.addWidget(self.permission_note)
        # Permission state is reported asynchronously by macOS: refresh the label while the dialog is open.
        if self.notifier:
            self.notifier.backend.refresh_status()
        self._status_timer = QTimer(self, interval=500, timeout=self._update_notify_status)
        self._status_timer.start()
        self._update_notify_status()
        self.notify_master.toggled.connect(self._update_notify_state)
        self._update_notify_state()
        lay.addStretch(1)
        return page

    def _update_notify_state(self) -> None:
        on = self.notify_master.isChecked()
        self.notify_box.setEnabled(on)
        for box in self.notify_toggles.values():
            box.setEnabled(on and bool(box.property("available")))
        self.test_btn.setEnabled(self.notifier is not None)

    def _update_notify_status(self) -> None:
        backend = self.notifier.backend if self.notifier else None
        if self._test_result is None:
            self.backend_note.setText(f"Delivered via {backend.name}." if backend else "Notifications unavailable.")
        self.permission_note.setText(f"Permission: {backend.status}" if backend and backend.status else "")
        if hasattr(self, "notify_perm_label"):
            self._update_notification_perm_row()

    def _send_test(self) -> None:
        # The app can only hand the notification to macOS; whether it is shown is up to macOS settings.
        if self.notifier and self.notifier.send_test():
            self._test_result = f"Test sent via {self.notifier.backend.name}. Nothing appeared? See below."
        else:
            self._test_result = "Could not send a notification on this system."
        self.backend_note.setText(self._test_result)
        if self.notifier:
            QTimer.singleShot(1500, self.notifier.backend.refresh_status)  # pick up a just-answered permission prompt

    def _previews_tab(self, s) -> QWidget:
        page, lay = self._page()
        prev = QGroupBox("Live previews")
        vf = QFormLayout(prev)
        self.grid_fps = self._dspin(0.5, 30, s.preview.grid_fps, " fps")
        vf.addRow("Grid frame rate", self.grid_fps)
        self.grid_width = self._spin(160, 1920, s.preview.grid_max_width, " px")
        vf.addRow("Grid capture width", self.grid_width)
        self.exp_fps = self._dspin(1, 60, s.preview.expanded_fps, " fps")
        vf.addRow("Expanded frame rate", self.exp_fps)
        self.exp_width = self._spin(480, 3840, s.preview.expanded_max_width, " px")
        vf.addRow("Expanded capture width", self.exp_width)
        self.tile_width = self._spin(260, 720, s.tile_width, " px")
        vf.addRow("Tile size", self.tile_width)
        self.backend = QComboBox()
        if system.IS_WINDOWS:
            from mcmanager.windows.win32 import CAPTURE_METHODS

            self.backend.addItem("Automatic (window content)", "auto")
            self.backend.addItem(CAPTURE_METHODS["screen"], "screen")
        else:
            sck_ok = ScreenCaptureKitBackend.available()
            self.backend.addItem("Automatic" + (" (ScreenCaptureKit)" if sck_ok else " (Quartz)"), "auto")
            self.backend.addItem("ScreenCaptureKit (macOS 14+)", "screencapturekit")
            self.backend.addItem("Quartz CGWindowList (legacy)", "quartz")
        self.backend.setCurrentIndex(max(0, self.backend.findData(s.preview.backend)))
        vf.addRow("Capture method", self.backend)
        vf.addRow("", _hint("Higher frame rates and widths look better but cost more CPU and GPU time."))
        lay.addWidget(prev)
        lay.addStretch(1)
        return page

    def _permissions_tab(self) -> QWidget:
        page, lay = self._page()
        perm = QGroupBox("macOS permissions" if system.IS_MAC else "Permissions")
        mf = QFormLayout(perm)
        if system.needs_privacy_permissions():
            mf.addRow("Screen Recording", self._perm_row(
                permissions.screen_capture_allowed(), "Required for live previews.",
                permissions.request_screen_capture, permissions.SCREEN_RECORDING_SETTINGS_URL))
            mf.addRow("Accessibility", self._perm_row(
                permissions.accessibility_trusted(),
                "Optional: restores minimized windows and closes games gracefully.",
                permissions.request_accessibility, permissions.ACCESSIBILITY_SETTINGS_URL))
        mf.addRow("Notifications", self._notification_perm_row())
        if system.needs_privacy_permissions():
            mf.addRow("", _hint(
                f"macOS grants these to {permissions.permission_holder()}. "
                "After changing a permission, quit and reopen this app."
            ))
        else:
            mf.addRow("", _hint(
                "Live previews and window control need no special permission here, and no administrator "
                "rights. (A game started “as administrator” can't be controlled by a normal app.)"
            ))
        lay.addWidget(perm)
        lay.addStretch(1)
        return page

    @staticmethod
    def _spin(lo, hi, value, suffix):
        w = QSpinBox()
        w.setRange(lo, hi)
        w.setSingleStep(40)
        w.setSuffix(suffix)
        w.setValue(value)
        return w

    @staticmethod
    def _dspin(lo, hi, value, suffix):
        w = QDoubleSpinBox()
        w.setRange(lo, hi)
        w.setDecimals(1)
        w.setSingleStep(1)
        w.setSuffix(suffix)
        w.setValue(value)
        return w

    def _with_browse(self, line: QLineEdit, callback):
        row = QHBoxLayout()
        row.addWidget(line, 1)
        b = QPushButton("Browse…")
        b.clicked.connect(callback)
        row.addWidget(b)
        return row

    def _notification_perm_row(self) -> QHBoxLayout:
        """Live notification permission (macOS reports it asynchronously; refreshed by _status_timer)."""
        row = QHBoxLayout()
        self.notify_perm_label = QLabel()
        self.notify_perm_label.setWordWrap(True)
        row.addWidget(self.notify_perm_label, 1)
        self.notify_perm_request = QPushButton("Request…")
        self.notify_perm_request.clicked.connect(self._request_notification_permission)
        row.addWidget(self.notify_perm_request)
        self.notify_perm_open = QPushButton("Open Settings")
        self.notify_perm_open.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(permissions.NOTIFICATIONS_SETTINGS_URL)))
        row.addWidget(self.notify_perm_open)
        self._update_notification_perm_row()
        return row

    def _request_notification_permission(self) -> None:
        if self.notifier:
            self.notifier.backend.request_permission()
            QTimer.singleShot(1500, self.notifier.backend.refresh_status)

    def _update_notification_perm_row(self) -> None:
        backend = self.notifier.backend if self.notifier else None
        state = backend.permission if backend else "unavailable"
        text = {
            "allowed": "✅ Allowed — crash, launch and other alerts can appear.",
            "denied": f"⚠️ Turned off — enable “{APP_NAME}” in System Settings › "
                      "Notifications. Alerts are shown inside the dashboard meanwhile.",
            "not determined": "⚠️ Not asked yet — macOS will ask the first time a notification is sent, "
                              "or press Request.",
            "unknown": "❔ Can't be checked when running from Terminal (macOS lists these under “Script "
                       "Editor”). Use the packaged app for its own notification permission.",
            "system": "❔ Sent to Windows. Whether they appear depends on Settings › System › Notifications "
                      "and Do not disturb, which apps can't check; use Send Test Notification.",
            "unavailable": "❔ Native notifications aren't available here; alerts appear inside the dashboard.",
        }.get(state, "❔ Checking…")
        if backend and getattr(backend, "auth", None) == "checking":
            text = "❔ Checking…"
        self.notify_perm_label.setText(text)
        self.notify_perm_request.setVisible(state == "not determined")
        self.notify_perm_open.setVisible(state in ("denied", "not determined", "unknown", "system"))

    def _perm_row(self, granted: bool, text: str, request, url: str):
        row = QHBoxLayout()
        state = QLabel(("✅ Granted" if granted else "⚠️ Not granted") + f" — {text}")
        state.setWordWrap(True)
        row.addWidget(state, 1)
        if not granted:
            ask = QPushButton("Request…")
            ask.clicked.connect(lambda: request())
            row.addWidget(ask)
            open_btn = QPushButton("Open Settings")
            open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
            row.addWidget(open_btn)
        return row

    def _browse_app(self):
        if system.IS_WINDOWS:
            start = str(self.manager.install.executable.parent) if self.manager.install.executable else ""
            path, _ = QFileDialog.getOpenFileName(self, "Choose Prism Launcher (prismlauncher.exe)", start,
                                                  "Prism Launcher (prismlauncher.exe);;Programs (*.exe)")
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Choose Prism Launcher", "/Applications",
                                                  "Applications (*.app);;All files (*)")
        if path:
            self.app_path.setText(path)

    def _browse_data(self):
        path = QFileDialog.getExistingDirectory(self, "Choose Prism data folder", str(self.manager.install.data_dir))
        if path:
            self.data_dir.setText(path)

    def _accept(self):
        s = self.manager.settings
        s.prism_app_path = self.app_path.text().strip() or None
        s.prism_data_dir = self.data_dir.text().strip() or None
        s.launch_timeout_s = self.timeout.value()
        s.tile_width = self.tile_width.value()
        s.favorites_first = self.favorites_first.isChecked()
        s.staggered_launch = self.staggered.isChecked()
        s.launch_delay_s = self.delay.value()
        s.notifications.enabled = self.notify_master.isChecked()
        for key, box in self.notify_toggles.items():
            if box.property("available"):
                setattr(s.notifications, key, box.isChecked())
        s.preview.grid_fps = self.grid_fps.value()
        s.preview.grid_max_width = self.grid_width.value()
        s.preview.expanded_fps = self.exp_fps.value()
        s.preview.expanded_max_width = self.exp_width.value()
        s.preview.backend = self.backend.currentData()
        self.accept()
