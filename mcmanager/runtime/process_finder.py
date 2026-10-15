"""Finding the Minecraft JVM that belongs to a given Prism instance.

Prism starts Minecraft with ``-Djava.library.path=<instance>/natives`` and the
working directory set to ``<instance>/minecraft``. We match on those, which
identifies the exact instance and never touches unrelated Java processes.

Every stop/kill is preceded by :func:`verify_process`, which re-checks the
PID's creation time and command line so a recycled PID can never be killed by
mistake.

Paths are compared with the platform's rules (``os.path.normcase``): on Windows that ignores
case and treats ``/`` and ``\\`` alike (Prism may write either), on macOS it compares exactly.
Stopping uses psutil's portable ``terminate()``/``kill()`` (SIGTERM/SIGKILL on macOS,
``TerminateProcess`` on Windows, where a *graceful* stop goes through the game window instead:
see ``mcmanager.windows``).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import psutil

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class GameProcess:
    pid: int
    create_time: float
    instance_dir: str


# Path comparison rules of the running OS (replaceable in tests to check Windows behaviour).
_norm = os.path.normcase
_PATHSEP = os.pathsep


def _game_dirs(instance_dir: Path) -> set[str]:
    return {_norm(str(instance_dir / "minecraft")), _norm(str(instance_dir / ".minecraft"))}


def process_matches(cmdline: list[str], cwd: str | None, instance_dir: Path) -> bool:
    natives = _norm(str(instance_dir / "natives"))
    for arg in cmdline:
        arg = _norm(arg)
        if natives not in arg:
            continue
        value = arg.split("=", 1)[1] if arg[:2] in ("-D", "-d") and "=" in arg else arg  # -d: normcase'd
        if natives in (v.rstrip("\\/") for v in value.split(_PATHSEP)):
            return True
    return cwd is not None and _norm(cwd).rstrip("\\/") in _game_dirs(instance_dir)


def _looks_like_java(name: str | None, cmdline: list[str]) -> bool:
    if name and "java" in name.lower():
        return True
    return bool(cmdline) and "java" in os.path.basename(cmdline[0]).lower()


def scan_game_processes(instance_dirs: Iterable[Path]) -> dict[str, list[GameProcess]]:
    """Map each instance directory to the game processes running from it."""
    dirs = [Path(d) for d in instance_dirs]
    found: dict[str, list[GameProcess]] = {str(d): [] for d in dirs}
    if not dirs:
        return found
    me = os.getpid()
    try:
        user = psutil.Process(me).username()
    except psutil.Error:
        user = None

    for proc in psutil.process_iter(["pid", "name", "username", "create_time"]):
        info = proc.info
        if info["pid"] == me or (user and info.get("username") != user):
            continue
        try:
            cmdline = proc.cmdline()
        except psutil.Error:
            continue
        joined = _norm("\0".join(cmdline))
        candidates = [d for d in dirs if _norm(str(d)) in joined]
        cwd = None
        if not candidates and _looks_like_java(info.get("name"), cmdline):
            try:
                cwd = proc.cwd()
            except psutil.Error:
                cwd = None
            candidates = [d for d in dirs if _norm(cwd).rstrip("\\/") in _game_dirs(d)] if cwd else []
        for d in candidates:
            if process_matches(cmdline, cwd, d):
                found[str(d)].append(GameProcess(info["pid"], info["create_time"], str(d)))

    for d, procs in found.items():
        procs.sort(key=lambda p: p.create_time)
        if len(procs) > 1:
            log.warning("Multiple game processes for %s: %s", d, [p.pid for p in procs])
    return found


def verify_process(proc: GameProcess) -> psutil.Process | None:
    """Return the live process only if it is still the same game process."""
    try:
        p = psutil.Process(proc.pid)
        if abs(p.create_time() - proc.create_time) > 0.01:
            return None
        try:
            cwd = p.cwd()
        except psutil.Error:
            cwd = None
        if not process_matches(p.cmdline(), cwd, Path(proc.instance_dir)):
            return None
        return p
    except psutil.Error:
        return None


def _stop(proc: GameProcess, force: bool) -> bool:
    action = "kill" if force else "terminate"
    p = verify_process(proc)
    if p is None:
        log.warning("Refusing to %s pid %s: it is no longer the tracked game process", action, proc.pid)
        return False
    try:
        p.kill() if force else p.terminate()
        log.info("Sent %s to game pid %s", action, proc.pid)
        return True
    except psutil.AccessDenied:
        log.warning("Not allowed to %s pid %s (started by another user or elevated?)", action, proc.pid)
        return False
    except psutil.Error as exc:
        log.warning("%s of pid %s failed: %s", action.capitalize(), proc.pid, exc)
        return False


def terminate_process(proc: GameProcess) -> bool:
    """Ask the game process to quit: SIGTERM on macOS (the JVM shuts down normally).

    On Windows this is ``TerminateProcess``, i.e. immediate; callers try closing the game's
    window first (``WindowService.close_gracefully``) so Minecraft can save.
    """
    return _stop(proc, force=False)


def kill_process(proc: GameProcess) -> bool:
    """Force quit: SIGKILL on macOS, ``TerminateProcess`` on Windows."""
    return _stop(proc, force=True)
