"""Helpers for running either from source (``python -m mcmanager``) or packaged.

The packaged app is built with PyInstaller (see ``packaging/``): a macOS ``.app`` bundle or a
Windows folder with an ``.exe``. Inside it:

* ``sys.frozen`` is set and ``sys.executable`` is the app's own binary
  (``…/Contents/MacOS/<app name>`` or ``<folder>\\<app name>.exe``), not a Python interpreter,
  so helper processes are started by re-running that binary with a flag.
* Code and resources live in the read-only bundle or program folder. User data never does: it
  stays in the user's profile (see ``mcmanager.paths``), so rebuilding, upgrading or replacing
  the app can't touch it.
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


def normalize_machine(machine: str) -> str:
    """One name per architecture: Windows says AMD64/ARM64 where macOS says x86_64/arm64."""
    m = machine.lower()
    return {"amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64"}.get(m, m)


def runtime_arch() -> dict:
    """CPU architecture this process runs as, and whether Rosetta 2 translates it (for logs/self-check).

    The app has separate Apple silicon (arm64) and Intel (x86_64) builds; an Intel build on an
    Apple silicon Mac runs under Rosetta, which works but is slower. ``rosetta`` is None where
    the question doesn't apply (Windows).
    """
    import platform
    import subprocess

    machine = normalize_machine(platform.machine())
    if sys.platform != "darwin":
        return {"machine": machine, "rosetta": None}
    translated = None
    try:
        out = subprocess.run(["/usr/sbin/sysctl", "-n", "sysctl.proc_translated"],
                             capture_output=True, text=True, timeout=2).stdout.strip()
        translated = out == "1" if out in ("0", "1") else False  # key is missing on Intel Macs
    except (OSError, subprocess.SubprocessError):
        pass
    return {"machine": machine, "rosetta": translated}


def external_env() -> dict[str, str]:
    """Environment for launching *other* programs (Prism, osascript): ours minus private variables.

    Matching is case-insensitive because Windows variable names are (``Qt_Plugin_Path`` is the
    same variable as ``QT_PLUGIN_PATH`` there).
    """
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(_PRIVATE_ENV_PREFIXES)}
    if sys.platform != "win32":
        env.setdefault("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    return env


def detached_process_kwargs(hide_console: bool = False) -> dict:
    """``subprocess.Popen`` options that let a started program outlive this app.

    POSIX: a new session, so closing the dashboard (or Ctrl+C in its terminal) doesn't reach
    the child. Windows: a new process group, detached from our console if we have one;
    ``hide_console`` also stops a console program (e.g. ``python.exe`` running the mock client)
    from opening a console window of its own.
    """
    if sys.platform != "win32":
        return {"start_new_session": True}
    import subprocess

    flags = subprocess.CREATE_NEW_PROCESS_GROUP
    flags |= subprocess.CREATE_NO_WINDOW if hide_console else subprocess.DETACHED_PROCESS
    return {"creationflags": flags, "close_fds": True}


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
