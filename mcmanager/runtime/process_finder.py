"""Finding the Minecraft JVM that belongs to a given Prism instance.

Prism starts Minecraft with ``-Djava.library.path=<instance>/natives`` and the
working directory set to ``<instance>/minecraft``. We match on those, which
identifies the exact instance and never touches unrelated Java processes.

Every signal we send is preceded by :func:`verify_process`, which re-checks the
PID's creation time and command line so a recycled PID can never be killed by
mistake.
"""

from __future__ import annotations

import logging
import os
import signal
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


def _game_dirs(instance_dir: Path) -> set[str]:
    return {str(instance_dir / "minecraft"), str(instance_dir / ".minecraft")}


def process_matches(cmdline: list[str], cwd: str | None, instance_dir: Path) -> bool:
    natives = str(instance_dir / "natives")
    for arg in cmdline:
        if natives not in arg:
            continue
        value = arg.split("=", 1)[1] if arg.startswith("-D") and "=" in arg else arg
        if natives in value.split(os.pathsep):
            return True
    return cwd is not None and cwd in _game_dirs(instance_dir)


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
        joined = "\0".join(cmdline)
        candidates = [d for d in dirs if str(d) in joined]
        cwd = None
        if not candidates and _looks_like_java(info.get("name"), cmdline):
            try:
                cwd = proc.cwd()
            except psutil.Error:
                cwd = None
            candidates = [d for d in dirs if cwd in _game_dirs(d)] if cwd else []
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


def send_signal(proc: GameProcess, sig: int = signal.SIGTERM) -> bool:
    p = verify_process(proc)
    if p is None:
        log.warning("Refusing to signal pid %s: it is no longer the tracked game process", proc.pid)
        return False
    try:
        p.send_signal(sig)
        log.info("Sent signal %s to game pid %s", sig, proc.pid)
        return True
    except psutil.Error as exc:
        log.warning("Signal %s to pid %s failed: %s", sig, proc.pid, exc)
        return False
