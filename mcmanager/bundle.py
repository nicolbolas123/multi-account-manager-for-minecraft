"""Helpers for running either from source (``python -m mcmanager``) or as the packaged .app.

The packaged app is built with PyInstaller (see ``packaging/``). Inside it:

* ``sys.frozen`` is set and ``sys.executable`` is the app's own binary
  (``…/Contents/MacOS/<app name>``), not a Python interpreter,
  so helper processes are started by re-running that binary with a flag.
* Code and resources live in the read-only bundle. User data never does: it
  stays in ``~/Library/Application Support/…`` (see ``mcmanager.paths``), so
  rebuilding or replacing the app can't touch it.
* PyInstaller and Qt may set environment variables for our own process. Other
  programs we start (Prism Launcher is itself a Qt app, ``osascript``) get a
  cleaned environment so those variables can't confuse them.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

FAKE_CLIENT_FLAG = "--mcmanager-fake-client"  # argv[1] that turns the frozen binary into the mock game

# Variables that only make sense for this process (PyInstaller bootloader, bundled Qt/Python).
_PRIVATE_ENV_PREFIXES = ("_PYI", "PYI_", "_MEIPASS", "QT_", "QML", "PYTHON", "DYLD_")


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """Directory that contains the ``mcmanager`` package's data files (bundle or source tree)."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def asset_dir() -> Path:
    return resource_root() / "mcmanager" / "ui" / "assets"


def app_bundle_path() -> Path | None:
    """The ``….app`` folder when running packaged, else None."""
    if not is_frozen():
        return None
    for parent in Path(sys.executable).resolve().parents:
        if parent.suffix == ".app":
            return parent
    return None


def runtime_arch() -> dict:
    """CPU architecture this process runs as, and whether Rosetta 2 translates it (for logs/self-check).

    The app has separate Apple silicon (arm64) and Intel (x86_64) builds; an Intel build on an
    Apple silicon Mac runs under Rosetta, which works but is slower.
    """
    import platform
    import subprocess

    translated = None
    try:
        out = subprocess.run(["/usr/sbin/sysctl", "-n", "sysctl.proc_translated"],
                             capture_output=True, text=True, timeout=2).stdout.strip()
        translated = out == "1" if out in ("0", "1") else False  # key is missing on Intel Macs
    except (OSError, subprocess.SubprocessError):
        pass
    return {"machine": platform.machine(), "rosetta": translated}


def external_env() -> dict[str, str]:
    """Environment for launching *other* programs (Prism, osascript): ours minus private variables."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(_PRIVATE_ENV_PREFIXES)}
    env.setdefault("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    return env


def fake_client_command() -> tuple[list[str], dict[str, str]]:
    """Command + environment that start the mock game client (``--mock`` mode only).

    From source: ``python -m mcmanager.mock.fake_client`` with the source tree on ``PYTHONPATH``
    (the client runs in the instance folder, so the current directory can't be relied on).
    Packaged: the app binary re-run with ``FAKE_CLIENT_FLAG``; ``PYINSTALLER_RESET_ENVIRONMENT``
    makes it start as a fresh, independent process.
    """
    if is_frozen():
        return [sys.executable, FAKE_CLIENT_FLAG], dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(resource_root()), env.get("PYTHONPATH")]))
    return [sys.executable, "-m", "mcmanager.mock.fake_client"], env
