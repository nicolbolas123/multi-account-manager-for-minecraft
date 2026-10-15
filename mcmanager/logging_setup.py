"""Application logging: rotating file in the user's Logs folder plus stderr (when there is one)."""

from __future__ import annotations

import logging
import logging.handlers
import sys
import threading
from pathlib import Path

from mcmanager.paths import logs_dir


def setup_logging(debug: bool = False) -> Path:
    directory = logs_dir()
    directory.mkdir(parents=True, exist_ok=True)
    log_file = directory / "app.log"

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    handlers: list[logging.Handler] = [file_handler]
    if sys.stderr is not None:  # a windowed Windows .exe has no stderr; then the file is the only log
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(fmt)
        handlers.append(stream_handler)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    root.handlers[:] = handlers
    _log_uncaught_exceptions()
    return log_file


def _log_uncaught_exceptions() -> None:
    """Write crashes (including ones in Qt slots and threads) to the log file.

    Without a console nobody would see the traceback otherwise; the log is what users attach to
    bug reports.
    """
    previous = sys.excepthook

    def hook(exc_type, exc, tb):
        if not issubclass(exc_type, KeyboardInterrupt):
            logging.getLogger("mcmanager").critical("Unhandled exception", exc_info=(exc_type, exc, tb))
        previous(exc_type, exc, tb)

    def thread_hook(args):
        logging.getLogger("mcmanager").critical(
            "Unhandled exception in thread %s", getattr(args.thread, "name", "?"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback))  # also on stderr, via the log

    sys.excepthook = hook
    threading.excepthook = thread_hook
