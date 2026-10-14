"""A stand-in "Minecraft client" process used only in ``--mock`` mode.

It is a tiny Python process whose command line mimics what Prism passes to
Java (``-Djava.library.path=<instance>/natives``), so the *real* process finder,
log parser and crash detection can be exercised without Minecraft. It writes
vanilla-style lines to ``<instance>/minecraft/logs/latest.log``.

Run as: python -m mcmanager.mock.fake_client -Djava.library.path=... --instance-dir DIR --player NAME
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance-dir", required=True)
    parser.add_argument("--player", default="MockPlayer")
    parser.add_argument("--server", default="mock.example.net")
    parser.add_argument("--crash-after", type=float, default=0.0)
    args, _unknown = parser.parse_known_args(argv)

    game = Path(args.instance_dir) / "minecraft"
    (game / "logs").mkdir(parents=True, exist_ok=True)
    log_path = game / "logs" / "latest.log"
    if log_path.exists():
        log_path.rename(game / "logs" / f"previous-{int(time.time())}.log")
    fh = open(log_path, "a", buffering=1, encoding="utf-8")

    def write(msg: str, thread: str = "Render thread") -> None:
        fh.write(f"[{time.strftime('%H:%M:%S')}] [{thread}/INFO]: {msg}\n")

    def on_term(_signo, _frame):
        write("Stopping!")
        fh.close()
        sys.exit(0)

    signal.signal(signal.SIGTERM, on_term)
    write(f"Setting user: {args.player}")
    time.sleep(2)
    write(f"Connecting to {args.server}, 25565")
    started = time.monotonic()
    while True:
        time.sleep(1)
        if args.crash_after and time.monotonic() - started > args.crash_after:
            reports = game / "crash-reports"
            reports.mkdir(exist_ok=True)
            (reports / f"crash-{time.strftime('%Y-%m-%d_%H.%M.%S')}-client.txt").write_text(
                "---- Minecraft Crash Report ----\n// Mock crash for testing\n"
            )
            write("Mock client crashing on purpose")
            os._exit(1)


if __name__ == "__main__":
    raise SystemExit(main())
