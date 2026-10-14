"""Application logging: rotating file in the user's Logs folder plus stderr."""

from __future__ import annotations

import logging
import logging.handlers
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
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    root.handlers[:] = [file_handler, stream_handler]
    return log_file
