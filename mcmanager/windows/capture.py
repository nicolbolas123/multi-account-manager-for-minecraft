"""Live window capture.

The Windows backend (``Win32CaptureBackend``, PrintWindow / screen copy) lives in
``mcmanager.windows.win32``; ``create_backend`` picks the right one for the platform.

macOS backends
--------------
``ScreenCaptureKitBackend`` (macOS 14+, preferred)
    Apple's supported capture API. Captures a single window even when it is
    covered by other windows, scaled to the requested size by the system.
``QuartzBackend`` (fallback)
    ``CGWindowListCreateImage``. Deprecated by Apple since macOS 14 and may be
    removed in a future release, but still functional on macOS 15.

Both require Screen Recording permission. Neither can capture a minimised
window (macOS does not render it); in that case the last frame is kept and the
UI shows that the preview is paused.

``CaptureWorker`` runs captures on a background thread at a per-target frame
rate and delivers ``QImage`` frames to the UI thread through a Qt signal.
"""

from __future__ import annotations

import logging
import platform
import sys
import threading
import time
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

from mcmanager.windows.discovery import WindowInfo

log = logging.getLogger(__name__)

SC_ERROR_USER_DECLINED = -3801


class CaptureError(Exception):
    def __init__(self, message: str, permission: bool = False):
        super().__init__(message)
        self.permission = permission


def target_size(src_w: int, src_h: int, max_width: int) -> tuple[int, int]:
    scale = min(1.0, max_width / src_w) if src_w > 0 else 1.0
    return max(1, round(src_w * scale)), max(1, round(src_h * scale))


def cgimage_to_qimage(cgimage, max_width: int) -> QImage:
    """Draw a CGImage into a BGRA bitmap of at most ``max_width`` pixels and wrap it as a QImage."""
    import Quartz

    src_w, src_h = Quartz.CGImageGetWidth(cgimage), Quartz.CGImageGetHeight(cgimage)
    w, h = target_size(src_w, src_h, max_width)
    bytes_per_row = w * 4
    buffer = bytearray(bytes_per_row * h)
    colorspace = Quartz.CGColorSpaceCreateWithName(Quartz.kCGColorSpaceSRGB)
    ctx = Quartz.CGBitmapContextCreate(
        buffer, w, h, 8, bytes_per_row, colorspace,
        Quartz.kCGImageAlphaPremultipliedFirst | Quartz.kCGBitmapByteOrder32Little,
    )
    if ctx is None:
        raise CaptureError("Could not create bitmap context")
    Quartz.CGContextSetInterpolationQuality(ctx, Quartz.kCGInterpolationMedium)
    Quartz.CGContextDrawImage(ctx, Quartz.CGRectMake(0, 0, w, h), cgimage)
    del ctx
    return QImage(bytes(buffer), w, h, bytes_per_row, QImage.Format.Format_ARGB32_Premultiplied).copy()


class CaptureBackend:
    name = "base"

    def capture(self, window: WindowInfo, max_width: int) -> QImage:
        raise NotImplementedError


class QuartzBackend(CaptureBackend):
    name = "quartz"

    def capture(self, window: WindowInfo, max_width: int) -> QImage:
        import Quartz

        image = Quartz.CGWindowListCreateImage(
            Quartz.CGRectNull,
            Quartz.kCGWindowListOptionIncludingWindow,
            window.window_id,
            Quartz.kCGWindowImageBoundsIgnoreFraming | Quartz.kCGWindowImageNominalResolution,
        )
        if image is None:
            if not Quartz.CGPreflightScreenCaptureAccess():
                raise CaptureError("Screen Recording permission is required", permission=True)
            raise CaptureError("Window could not be captured")
        if Quartz.CGImageGetWidth(image) <= 1:
            raise CaptureError("Window is not rendered (minimized or hidden)")
        return cgimage_to_qimage(image, max_width)


class ScreenCaptureKitBackend(CaptureBackend):
    name = "screencapturekit"
    TIMEOUT_S = 3.0
    REFRESH_INTERVAL_S = 1.0

    def __init__(self):
        import ScreenCaptureKit  # noqa: F401  (fail early if unavailable)

        self._windows: dict[int, object] = {}
        self._last_refresh = 0.0

    @staticmethod
    def available() -> bool:
        try:
            major = int(platform.mac_ver()[0].split(".")[0])
            import ScreenCaptureKit as SCK

            return major >= 14 and hasattr(SCK, "SCScreenshotManager")
        except (ValueError, ImportError):
            return False

    @staticmethod
    def _raise_for(error, default: str):
        if error is None:
            raise CaptureError(default)
        code = error.code()
        msg = str(error.localizedDescription())
        raise CaptureError(msg, permission=code == SC_ERROR_USER_DECLINED)

    def _refresh(self) -> None:
        import ScreenCaptureKit as SCK

        done = threading.Event()
        result: dict = {}

        def handler(content, error):
            result["content"], result["error"] = content, error
            done.set()

        SCK.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
            True, False, handler
        )
        self._last_refresh = time.monotonic()
        if not done.wait(self.TIMEOUT_S):
            raise CaptureError("Timed out listing windows")
        content = result.get("content")
        if content is None:
            self._raise_for(result.get("error"), "Could not list windows")
        self._windows = {int(w.windowID()): w for w in content.windows()}

    def capture(self, window: WindowInfo, max_width: int) -> QImage:
        import ScreenCaptureKit as SCK

        sc_window = self._windows.get(window.window_id)
        if sc_window is None:
            if time.monotonic() - self._last_refresh < self.REFRESH_INTERVAL_S:
                raise CaptureError("Window not available for capture yet")
            self._refresh()
            sc_window = self._windows.get(window.window_id)
            if sc_window is None:
                raise CaptureError("Window not available for capture")

        content_filter = SCK.SCContentFilter.alloc().initWithDesktopIndependentWindow_(sc_window)
        frame = sc_window.frame()
        # Native pixel size of the window (points x backing scale), capped at max_width.
        scale = float(content_filter.pointPixelScale()) if hasattr(content_filter, "pointPixelScale") else 2.0
        w, h = target_size(int(frame.size.width * scale), int(frame.size.height * scale), max_width)
        config = SCK.SCStreamConfiguration.alloc().init()
        config.setWidth_(w)
        config.setHeight_(h)
        config.setShowsCursor_(False)
        # Without these, content smaller/larger than the buffer is not scaled into it.
        if hasattr(config, "setScalesToFit_"):
            config.setScalesToFit_(True)
        if hasattr(config, "setPreservesAspectRatio_"):
            config.setPreservesAspectRatio_(True)
        if hasattr(config, "setIgnoreShadowsSingleWindow_"):
            config.setIgnoreShadowsSingleWindow_(True)

        done = threading.Event()
        result: dict = {}

        def handler(image, error):
            result["image"], result["error"] = image, error
            done.set()

        SCK.SCScreenshotManager.captureImageWithFilter_configuration_completionHandler_(content_filter, config, handler)
        if not done.wait(self.TIMEOUT_S):
            raise CaptureError("Timed out capturing window")
        image = result.get("image")
        if image is None:
            self._windows.pop(window.window_id, None)  # stale; re-list next time
            self._raise_for(result.get("error"), "Window could not be captured")
        return cgimage_to_qimage(image, max_width)


def create_backend(name: str) -> CaptureBackend:
    """Capture backend for the ``preview.backend`` setting on this platform.

    macOS: ``auto`` | ``screencapturekit`` | ``quartz``. Windows: ``auto``/``printwindow`` |
    ``screen``; a macOS value (e.g. from settings copied between machines) means automatic.
    """
    if sys.platform == "win32":
        from mcmanager.windows.win32 import Win32CaptureBackend

        return Win32CaptureBackend("screen" if name == "screen" else "printwindow")
    if name == "quartz":
        return QuartzBackend()
    if name == "screencapturekit" or (name == "auto" and ScreenCaptureKitBackend.available()):
        try:
            return ScreenCaptureKitBackend()
        except ImportError:
            log.warning("ScreenCaptureKit unavailable; falling back to Quartz capture")
    return QuartzBackend()


@dataclass
class CaptureTarget:
    window: WindowInfo
    fps: float
    max_width: int


class CaptureWorker(QObject):
    """Background capture loop. Thread-safe: call ``set_targets`` from the UI thread."""

    frame_ready = Signal(str, QImage)
    capture_failed = Signal(str, str, bool)  # key, message, is_permission_problem

    def __init__(self, backend: CaptureBackend):
        super().__init__()
        self._backend = backend
        self._targets: dict[str, CaptureTarget] = {}
        self._next_due: dict[str, float] = {}
        self._failures: dict[str, int] = {}
        self._cond = threading.Condition()
        self._running = False
        self._thread: threading.Thread | None = None

    @property
    def backend_name(self) -> str:
        return self._backend.name

    def set_backend(self, backend: CaptureBackend) -> None:
        with self._cond:
            self._backend = backend
            self._failures.clear()
            self._cond.notify()

    def set_targets(self, targets: dict[str, CaptureTarget]) -> None:
        with self._cond:
            for key in list(self._next_due):
                if key not in targets:
                    self._next_due.pop(key, None)
                    self._failures.pop(key, None)
            for key in targets:
                old = self._targets.get(key)
                if old is None or old.window.window_id != targets[key].window.window_id:
                    self._next_due[key] = 0.0
                    self._failures.pop(key, None)
            self._targets = dict(targets)
            self._cond.notify()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="capture", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._cond:
            self._running = False
            self._cond.notify()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        try:
            import objc

            pool = objc.autorelease_pool
        except ImportError:  # non-macOS
            from contextlib import nullcontext as pool
        while True:
            with self._cond:
                if not self._running:
                    return
                now = time.monotonic()
                due = [k for k, t in self._next_due.items() if t <= now and k in self._targets]
                if not due:
                    wait = min(self._next_due.values(), default=now + 0.5) - now
                    self._cond.wait(timeout=max(0.005, min(wait, 0.5)))
                    continue
                key = min(due, key=lambda k: self._next_due[k])
                target, backend = self._targets[key], self._backend
                self._next_due[key] = now + 1.0 / max(0.2, target.fps)

            with pool():
                try:
                    image = backend.capture(target.window, target.max_width)
                except CaptureError as exc:
                    self._on_failure(key, str(exc), exc.permission)
                    continue
                except Exception as exc:  # never let the capture thread die
                    log.exception("Capture failed for %s", key)
                    self._on_failure(key, f"Capture error: {exc}", False)
                    continue
            with self._cond:
                self._failures.pop(key, None)
            self.frame_ready.emit(key, image)

    def _on_failure(self, key: str, message: str, permission: bool) -> None:
        with self._cond:
            count = self._failures.get(key, 0) + 1
            self._failures[key] = count
            if key in self._next_due:
                self._next_due[key] = time.monotonic() + min(5.0, 0.5 * count)
        if count in (1, 10) or count % 50 == 0:
            log.info("Capture of %s failed (%d): %s", key, count, message)
        self.capture_failed.emit(key, message, permission)
