"""Optional user notifications (crash, launch failure, bulk launch results...).

Policy (``Notifier``)
    Receives ``AppEvent``s from the manager, drops those whose category is
    disabled (or when the master switch is off), drops duplicates by event key,
    and hands the rest to a backend. Essential errors are unaffected: the
    manager still shows them in the window (toasts, card status) and logs them.

Backends
    ``UserNotificationsBackend`` (packaged .app): Apple's UserNotifications
    framework. The app appears under its own name in System Settings ›
    Notifications, macOS asks for permission the first time, and the current
    permission state can be read and shown in Settings.
    ``MacNotificationBackend`` (running from source / Terminal): ``osascript``
    ``display notification``. A process without its own app bundle can't use
    UserNotifications, so these notifications are attributed to "Script
    Editor" and are silently dropped unless Script Editor may notify. Delivery
    can't be confirmed, so Settings has a "Send Test Notification" button.
    ``TrayNotificationBackend`` (Windows): a notification-area (tray) icon whose
    ``showMessage`` Windows 10/11 shows as a toast notification. Needs no extra
    package and no registration; whether it appears is up to Windows' notification
    and Do-not-disturb settings, which an app can't read, so Settings offers a test.
    ``InAppBackend`` shows the notification as a toast in the dashboard; it is
    the fallback where native notifications are unavailable.

Server disconnects can't be detected reliably (the client log has no
dependable "disconnected" line; see README), so that category is shown as
unavailable instead of being guessed from a process exit.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import uuid
from typing import Callable

from mcmanager import APP_NAME
from mcmanager.bundle import external_env
from mcmanager.config.settings import NotificationSettings
from mcmanager.runtime.models import AppEvent

log = logging.getLogger(__name__)

# Categories that have a reliable event source. Others are shown as unavailable.
AVAILABLE_CATEGORIES = {"crashed", "launch_failed", "started", "bulk_completed", "bulk_failed"}
UNAVAILABLE_REASON = {
    "disconnected": "Not available: Minecraft's log does not reliably record when a client loses its "
                    "server connection, so this can't be detected without a game-side mod.",
}
MAX_REMEMBERED_KEYS = 2000


class NotificationBackend:
    name = "none"
    native = False
    status = ""  # human-readable permission/delivery state for Settings
    # Permission as the OS reports it: "allowed" | "denied" | "not determined" | "unknown" (macOS,
    # can't be read for this backend) | "system" (Windows: delivered, but its settings can't be
    # read) | "unavailable" (no native notifications here)
    permission = "unavailable"

    def request_permission(self) -> None:
        """Ask macOS for permission (shows its prompt if the user hasn't decided yet)."""

    def refresh_status(self, callback: Callable[[str], None] | None = None) -> None:
        if callback:
            callback(self.status)

    def send(self, title: str, message: str) -> bool:
        raise NotImplementedError

    def send_event(self, event: AppEvent) -> bool:
        return self.send(event.title, event.message)


class MacNotificationBackend(NotificationBackend):
    name = "macOS Notification Center (via Script Editor)"
    native = True
    permission = "unknown"
    status = "Can't be checked: allow notifications for Script Editor in System Settings › Notifications."

    def __init__(self):
        self._osascript = shutil.which("osascript")

    @staticmethod
    def available() -> bool:
        return sys.platform == "darwin" and shutil.which("osascript") is not None

    def send(self, title: str, message: str) -> bool:
        # Text is passed as arguments (never interpolated into the script), so quotes can't break it.
        script = ["on run argv", "display notification (item 2 of argv) with title (item 1 of argv)", "end run"]
        cmd = [self._osascript]
        for line in script:
            cmd += ["-e", line]
        try:
            subprocess.Popen(cmd + [title, message], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True, env=external_env())
            return True
        except OSError:
            log.exception("Could not post a macOS notification")
            return False


# UNAuthorizationStatus values
_AUTH_STATUS = {0: "not determined", 1: "denied", 2: "allowed", 3: "allowed", 4: "allowed"}


class UserNotificationsBackend(NotificationBackend):
    """Native notifications for the packaged app (own name and icon in Notification Center)."""

    name = "macOS Notification Center"
    native = True

    def __init__(self, fallback: NotificationBackend | None = None):
        import UserNotifications as UN

        self._UN = UN
        self._fallback = fallback  # used if the system refuses this app (e.g. unusual signing state)
        self._center = UN.UNUserNotificationCenter.currentNotificationCenter()
        self._delegate = _foreground_delegate()  # show banners even while the dashboard is frontmost
        self._center.setDelegate_(self._delegate)
        self.auth = "checking"
        self.status = "Checking…"
        self.refresh_status()

    @property
    def permission(self) -> str:
        return self.auth if self.auth in ("allowed", "denied", "not determined") else "unknown"

    def request_permission(self) -> None:
        def handler(granted, error):
            if error is not None:
                log.warning("Notification permission request failed: %s", error)
                return
            self.auth = "allowed" if granted else "denied"
            self.status = self._describe()

        self._center.requestAuthorizationWithOptions_completionHandler_(
            self._UN.UNAuthorizationOptionAlert | self._UN.UNAuthorizationOptionSound, handler)

    @staticmethod
    def available() -> bool:
        """Only inside our own .app: UserNotifications aborts the process if there is no app bundle."""
        if sys.platform != "darwin":
            return False
        try:
            import UserNotifications  # noqa: F401
            from Foundation import NSBundle

            from mcmanager import APP_BUNDLE_ID
        except ImportError:
            return False
        return NSBundle.mainBundle().bundleIdentifier() == APP_BUNDLE_ID

    def _describe(self) -> str:
        return {
            "allowed": "Allowed.",
            "denied": f"Turned off for this app. Turn on {APP_NAME} in System Settings › "
                      "Notifications (notifications are shown inside the dashboard meanwhile).",
            "not determined": "macOS will ask for permission the first time a notification is sent.",
        }.get(self.auth, self.auth)

    def refresh_status(self, callback: Callable[[str], None] | None = None) -> None:
        def handler(settings):
            self.auth = _AUTH_STATUS.get(int(settings.authorizationStatus()), "unknown")
            self.status = self._describe()
            if callback:
                callback(self.status)

        self._center.getNotificationSettingsWithCompletionHandler_(handler)

    def send(self, title: str, message: str) -> bool:
        if self.auth == "denied":
            return False  # the Notifier then uses its in-app fallback
        UN = self._UN

        def post():
            content = UN.UNMutableNotificationContent.alloc().init()
            content.setTitle_(title)
            content.setBody_(message)
            content.setSound_(UN.UNNotificationSound.defaultSound())
            request = UN.UNNotificationRequest.requestWithIdentifier_content_trigger_(str(uuid.uuid4()), content, None)
            self._center.addNotificationRequest_withCompletionHandler_(
                request, lambda error: error and log.warning("Notification not delivered: %s", error))

        def authorized(granted, error):
            # Asking again after the user has decided returns at once, without a prompt.
            if error is not None:
                log.warning("UserNotifications unavailable (%s); falling back", error)
                self.auth, self.status = "unavailable", f"Unavailable ({error.localizedDescription()})."
                if self._fallback:
                    self._fallback.send(title, message)
                return
            self.auth = "allowed" if granted else "denied"
            self.status = self._describe()
            if granted:
                post()
            else:
                log.info("Notification permission denied; notification not shown by macOS")

        self._center.requestAuthorizationWithOptions_completionHandler_(
            UN.UNAuthorizationOptionAlert | UN.UNAuthorizationOptionSound, authorized)
        return True


_delegate_class = None


def _foreground_delegate():
    """A UNUserNotificationCenterDelegate that lets banners appear while our app is active."""
    global _delegate_class
    if _delegate_class is None:
        import objc
        import UserNotifications as UN
        from Foundation import NSObject

        options = (UN.UNNotificationPresentationOptionBanner | UN.UNNotificationPresentationOptionList
                   | UN.UNNotificationPresentationOptionSound)

        class ForegroundNotificationDelegate(NSObject, protocols=[objc.protocolNamed("UNUserNotificationCenterDelegate")]):
            def userNotificationCenter_willPresentNotification_withCompletionHandler_(self, center, note, handler):
                handler(options)

        _delegate_class = ForegroundNotificationDelegate
    return _delegate_class.alloc().init()


class TrayNotificationBackend(NotificationBackend):
    """Windows notifications through a notification-area icon (shown as toasts on Windows 10/11)."""

    name = "Windows notifications"
    native = True
    permission = "system"
    status = ("Windows shows these as notifications. Nothing appeared? Check Settings › System › "
              "Notifications (and Do not disturb / Focus).")

    def __init__(self):
        from PySide6.QtWidgets import QApplication, QSystemTrayIcon

        app = QApplication.instance()
        self._tray = QSystemTrayIcon(app.windowIcon(), app)
        self._tray.setToolTip(APP_NAME)
        self._tray.activated.connect(lambda _reason: self.on_activate and self.on_activate())
        self.on_activate: Callable[[], None] | None = None  # e.g. show the dashboard
        self._tray.show()
        app.aboutToQuit.connect(self._tray.hide)  # don't leave a dead icon behind

    @staticmethod
    def available() -> bool:
        if sys.platform != "win32":
            return False
        from PySide6.QtWidgets import QApplication, QSystemTrayIcon

        return QApplication.instance() is not None and QSystemTrayIcon.isSystemTrayAvailable()

    def send(self, title: str, message: str) -> bool:
        from PySide6.QtWidgets import QSystemTrayIcon

        if not self._tray.isVisible():
            self._tray.show()
        self._tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 8000)
        return True


def native_backend() -> NotificationBackend | None:
    """The best native backend for how the app is running, or None."""
    if TrayNotificationBackend.available():
        try:
            return TrayNotificationBackend()
        except Exception:  # never let notifications stop the app from starting
            log.exception("Could not set up Windows notifications")
            return None
    if UserNotificationsBackend.available():
        try:
            return UserNotificationsBackend(fallback=MacNotificationBackend() if MacNotificationBackend.available() else None)
        except Exception:  # never let notifications stop the app from starting
            log.exception("Could not set up UserNotifications")
    if MacNotificationBackend.available():
        return MacNotificationBackend()
    return None


class InAppBackend(NotificationBackend):
    name = "in-app messages"
    status = "Native notifications aren't available here; they appear inside the dashboard."

    def __init__(self, show: Callable[[str, str], None]):
        self._show = show

    def send(self, title: str, message: str) -> bool:
        self._show(title, message)
        return True

    def send_event(self, event: AppEvent) -> bool:
        if event.shown_in_app:
            return True  # already on screen as an error/warning toast; don't show it twice
        return self.send(event.title, event.message)


class Notifier:
    def __init__(self, settings: NotificationSettings | Callable[[], NotificationSettings],
                 backend: NotificationBackend, fallback: NotificationBackend | None = None):
        self._settings = settings if callable(settings) else (lambda: settings)
        self.backend = backend
        self.fallback = fallback
        self._seen: dict[str, None] = {}  # insertion-ordered set of delivered event keys
        self.sent: list[AppEvent] = []  # delivered events (for the UI and tests)

    @property
    def settings(self) -> NotificationSettings:
        return self._settings()

    def wants(self, event: AppEvent) -> bool:
        s = self.settings
        return any(c in AVAILABLE_CATEGORIES and s.allows(c) for c in event.categories)

    def handle(self, event: AppEvent) -> bool:
        """Deliver ``event`` if enabled and not already delivered. Returns True if sent."""
        if event.key in self._seen:
            log.debug("Duplicate notification suppressed: %s", event.key)
            return False
        if not self.wants(event):
            log.debug("Notification %s suppressed by settings", event.category)
            return False
        self._seen[event.key] = None
        while len(self._seen) > MAX_REMEMBERED_KEYS:
            self._seen.pop(next(iter(self._seen)))
        log.info("Notification [%s]: %s — %s", event.category, event.title, event.message)
        self.sent.append(event)
        del self.sent[:-100]
        if self.backend.send_event(event):
            return True
        return bool(self.fallback and self.fallback.send_event(event))

    def send_test(self) -> bool:
        return self.backend.send(APP_NAME, "Notifications are working.")
