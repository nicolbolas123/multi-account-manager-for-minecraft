"""Application entry point: argument parsing and wiring of the layers."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
from dataclasses import dataclass
from pathlib import Path

from mcmanager import APP_ID, APP_NAME, __version__
from mcmanager.logging_setup import setup_logging
from mcmanager.paths import app_support_dir, cache_dir, logs_dir

log = logging.getLogger("mcmanager")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="mcmanager", description=APP_NAME)
    parser.add_argument("--mock", action="store_true",
                        help="use simulated Prism instances, processes and previews (does not touch real data)")
    parser.add_argument("--debug", action="store_true", help="verbose logging")
    parser.add_argument("--screenshot", metavar="PNG",
                        help="(testing) save a screenshot of the window after a few seconds and quit")
    parser.add_argument("--screenshot-delay", type=float, default=6.0, help=argparse.SUPPRESS)
    parser.add_argument("--screenshot-expand", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--launch-all-on-start", action="store_true", help=argparse.SUPPRESS)  # --mock only
    parser.add_argument("--self-check", action="store_true",
                        help="check that bundled dependencies and resources load, print a report and exit")
    # Finder/LaunchServices can add arguments of its own (e.g. -psn_… on older macOS): ignore
    # anything unknown instead of exiting, which would look like the app failing to open.
    args, unknown = parser.parse_known_args(argv)
    if unknown:
        logging.getLogger("mcmanager").info("Ignoring unrecognised arguments: %s", unknown)
    return args


def settings_path(args: argparse.Namespace) -> Path:
    if args.mock:
        return cache_dir() / "mock" / "mock-settings.json"
    # MCMANAGER_SETTINGS lets tests use a throwaway settings file.
    return Path(os.environ.get("MCMANAGER_SETTINGS") or app_support_dir() / "settings.json")


@dataclass
class AppContext:
    app: object
    window: object
    manager: object
    previews: object
    worker: object
    notifier: object = None


def build(args: argparse.Namespace) -> AppContext:
    """Create the QApplication and wire all layers together (does not start threads)."""
    from PySide6.QtWidgets import QApplication

    from mcmanager.config import SettingsStore
    from mcmanager.runtime.manager import InstanceManager, PrismLaunchBackend
    from mcmanager.runtime.previews import PreviewCoordinator
    from mcmanager.ui.main_window import MainWindow
    from mcmanager.ui.theme import apply_theme
    from mcmanager.windows.capture import CaptureWorker, create_backend

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_ID)
    apply_theme(app)
    from PySide6.QtGui import QIcon

    from mcmanager.bundle import asset_dir

    icon = asset_dir() / "app-icon.png"
    if icon.is_file():  # Dock/window icon when run from source (the .app uses its own .icns)
        app.setWindowIcon(QIcon(str(icon)))

    if args.mock:
        from mcmanager.mock.environment import (
            MockCaptureBackend,
            MockLaunchBackend,
            MockWindowService,
            create_mock_prism,
        )

        store = SettingsStore(settings_path(args))
        settings = store.load()
        install = create_mock_prism(cache_dir() / "mock" / "prism")
        launch_backend, window_service = MockLaunchBackend(), MockWindowService()
        backend = MockCaptureBackend()
        locate = None
        log.warning("MOCK MODE: using simulated data in %s", cache_dir() / "mock")
    else:
        from mcmanager.prism import locate_prism
        from mcmanager.windows.service import create_window_service

        store = SettingsStore(settings_path(args))
        settings = store.load()
        locate = locate_prism
        install = locate_prism(settings.prism_app_path, settings.prism_data_dir)
        launch_backend, window_service = PrismLaunchBackend(), create_window_service()
        backend = create_backend(settings.preview.backend)
    log.info("Settings file: %s; capture backend: %s", store.path, backend.name)

    manager = InstanceManager(settings, store, install, launch_backend, window_service, locate=locate)
    worker = CaptureWorker(backend)
    previews = PreviewCoordinator(manager, worker)
    window = MainWindow(manager, previews, mock=args.mock)

    from mcmanager.notifications import InAppBackend, Notifier, native_backend

    in_app = InAppBackend(lambda title, message: window.toasts.show_message("info", f"{title}. {message}"))
    native = native_backend()
    log.info("Notifications: %s", native.name if native else "in-app only (no native backend)")
    if native is not None and hasattr(native, "on_activate"):  # Windows tray icon: click shows the dashboard
        native.on_activate = lambda: (window.showNormal(), window.raise_(), window.activateWindow())
    notifier = Notifier(lambda: manager.settings.notifications, native or in_app, fallback=in_app)
    manager.app_event.connect(notifier.handle)
    window.notifier = notifier

    def shutdown():
        worker.stop()
        manager.shutdown()

    app.aboutToQuit.connect(shutdown)
    return AppContext(app, window, manager, previews, worker, notifier)


def self_check() -> int:
    """Verify that everything the app needs at runtime is present (used by the build script)."""
    import importlib
    import json

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QImage, QImageReader
    from PySide6.QtWidgets import QApplication

    from mcmanager import bundle
    from mcmanager.prism import locate_prism

    app = QApplication.instance() or QApplication(sys.argv[:1])  # noqa: F841 (needed for image loading)
    report: dict = {"frozen": bundle.is_frozen(), "bundle": str(bundle.app_bundle_path() or ""), "problems": []}
    report.update(bundle.runtime_arch())
    report["platform"] = sys.platform
    modules = ["PySide6.QtWidgets", "PySide6.QtSvg", "psutil", "mcmanager.mock.fake_client"]
    if sys.platform == "darwin":
        modules += ["objc", "Foundation", "AppKit", "Quartz", "ScreenCaptureKit", "ApplicationServices",
                    "UserNotifications"]
    elif sys.platform == "win32":
        modules += ["winreg", "mcmanager.windows.win32"]
    for module in modules:
        try:
            importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001 - report every failure
            report["problems"].append(f"import {module}: {exc}")
    if sys.platform == "win32":
        try:  # the Win32 API is reachable and a window enumeration works
            from mcmanager.windows import win32

            win32.find_windows({os.getpid()})
        except Exception as exc:  # noqa: BLE001
            report["problems"].append(f"Win32 window API: {exc}")
    formats = {bytes(f).decode() for f in QImageReader.supportedImageFormats()}
    if "svg" not in formats:
        report["problems"].append("Qt SVG image plugin missing")
    for name in ("check.svg", "check-dim.svg", "app-icon.png"):
        path = bundle.asset_dir() / name
        if not path.is_file() or QImage(str(path)).isNull():
            report["problems"].append(f"asset not loadable: {path}")
    try:
        from Foundation import NSBundle

        report["bundle_id"] = str(NSBundle.mainBundle().bundleIdentifier() or "")
    except ImportError:
        pass
    install = locate_prism()
    report["prism"] = str(install.app_bundle or install.executable or "not found")
    report["prism_data"] = str(install.data_dir)
    report["user_data"] = str(app_support_dir())
    from mcmanager.notifications import native_backend

    backend = native_backend()
    if backend is not None:
        import time

        for _ in range(20):  # macOS answers asynchronously
            if getattr(backend, "auth", "") != "checking":
                break
            time.sleep(0.1)
    report["notifications"] = {"backend": backend.name if backend else "in-app only",
                               "permission": backend.permission if backend else "unavailable"}
    report["private_env_vars"] = sorted(k for k in os.environ if k.startswith(bundle._PRIVATE_ENV_PREFIXES))
    report["ok"] = not report["problems"]
    text = json.dumps(report, indent=2)
    if sys.stdout is not None:
        print(text)
    else:  # windowed Windows .exe: no console to print to (the "(console)" .exe prints it)
        out = logs_dir() / "self-check.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    return 0 if report["ok"] else 1


def _acquire_single_instance_lock(path: Path):
    """One running copy per settings file, so two copies can't overwrite each other's data."""
    from PySide6.QtCore import QLockFile

    path.parent.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(path.with_suffix(".lock")))
    lock.setStaleLockTime(0)  # a lock left by a crashed copy is detected by its (dead) process id instead
    return lock if lock.tryLock(200) else None


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_check:
        return self_check()
    log_file = setup_logging(args.debug)
    log.info("%s %s starting (python %s, %s)", APP_NAME, __version__, sys.version.split()[0], sys.platform)
    log.info("Log file: %s", log_file)
    from mcmanager import bundle

    if bundle.is_frozen():
        log.info("Running packaged from %s", bundle.app_bundle_path())
    log.info("CPU architecture: %(machine)s (Rosetta: %(rosetta)s)", bundle.runtime_arch())

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication(sys.argv[:1])
    lock = _acquire_single_instance_lock(settings_path(args))
    if lock is None:
        log.warning("Another copy is already running with %s; exiting", settings_path(args))
        QMessageBox.information(None, APP_NAME, f"{APP_NAME} is already open.\n\n"
                                "Only one copy can run at a time so they can't overwrite each other's settings "
                                "and play-time data. Switch to the open window (it may be on another desktop).")
        return 0

    ctx = build(args)
    app, window, manager = ctx.app, ctx.window, ctx.manager

    # Let Ctrl+C in the terminal quit cleanly.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    keepalive = QTimer(interval=250, timeout=lambda: None)
    keepalive.start()

    if args.launch_all_on_start:
        if not args.mock:  # never launch real accounts unattended
            log.warning("--launch-all-on-start is only allowed together with --mock; ignored")
        else:
            def launch_all():
                if not manager.entries:
                    manager.import_all_instances()
                manager.launch_many([e.id for e in manager.entries])
            QTimer.singleShot(1000, launch_all)

    if args.screenshot:
        def snap():
            if args.screenshot_expand and manager.entries:
                window.expand(manager.entries[0].id)
            QTimer.singleShot(1500, lambda: (window.grab().save(args.screenshot), app.quit()))
        QTimer.singleShot(int(args.screenshot_delay * 1000), snap)

    ctx.worker.start()
    manager.start()
    window.show()
    return app.exec()
