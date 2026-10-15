"""Launching instances through Prism Launcher's documented CLI.

Supported flags (from ``prismlauncher --help``, Prism 11.1.1):

    -l, --launch <instance>   launch instance by ID (its folder name)
    -a, --profile <profile>   account profile name to use with --launch
    -s, --server <address>    join a server on launch
        --show <instance>     open the instance window in Prism
    -d, --dir <directory>     use a custom data directory

Prism is single-instance: if it is already running, a second invocation hands
the command to the running launcher and exits. Either way the Minecraft JVM is
*not* our child process, so :mod:`mcmanager.runtime.process_finder` locates it
afterwards.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from mcmanager.bundle import detached_process_kwargs, external_env
from mcmanager.paths import logs_dir
from mcmanager.prism.locator import PrismInstallation

log = logging.getLogger(__name__)


class LaunchError(RuntimeError):
    pass


@dataclass
class LaunchRequest:
    instance_id: str
    profile: str | None = None
    server: str | None = None


def build_launch_command(install: PrismInstallation, request: LaunchRequest) -> list[str]:
    if not install.can_launch:
        raise LaunchError("Prism Launcher was not found. Configure its location in Settings.")
    cmd = [str(install.executable)]
    if install.data_dir_overridden:
        cmd += ["--dir", str(install.data_dir)]
    cmd += ["--launch", request.instance_id]
    if request.profile:
        cmd += ["--profile", request.profile]
    if request.server:
        cmd += ["--server", request.server]
    return cmd


def build_show_command(install: PrismInstallation, instance_id: str) -> list[str]:
    if not install.can_launch:
        raise LaunchError("Prism Launcher was not found. Configure its location in Settings.")
    cmd = [str(install.executable)]
    if install.data_dir_overridden:
        cmd += ["--dir", str(install.data_dir)]
    return cmd + ["--show", instance_id]


def spawn_prism(cmd: list[str]) -> subprocess.Popen:
    """Start Prism detached from our process group so it outlives this app.

    The command is passed as a list (never through a shell), so paths with spaces need no
    quoting on either platform. Output goes to a log file in our Logs folder for troubleshooting.
    """
    log.info("Running: %s", subprocess.list2cmdline(cmd))
    out_path: Path = logs_dir() / "prism-cli.log"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out = open(out_path, "ab")
    try:
        return subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            env=external_env(),  # Prism is a Qt app too: don't hand it our bundle's Qt/Python variables
            **detached_process_kwargs(),
        )
    except OSError as exc:
        raise LaunchError(f"Could not start Prism Launcher: {exc}") from exc
    finally:
        out.close()
