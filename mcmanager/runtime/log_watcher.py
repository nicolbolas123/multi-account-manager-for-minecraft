"""Incremental reader for Minecraft's ``logs/latest.log``.

Used for best-effort facts that Prism does not expose: the logged-in username
and whether the client is on a server, in singleplayer, or in menus. The
patterns match vanilla client log lines; mods or future versions may change
them, so the dashboard labels this information as approximate.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from mcmanager.runtime.models import LogState

_USER = re.compile(r"Setting user: (\S+)")
_CONNECT = re.compile(r"Connecting to ([^,\s]+), ?(\d+)")
_SINGLEPLAYER = re.compile(r"Starting integrated minecraft server")
_LEFT_WORLD = re.compile(r"Stopping server|Stopping singleplayer server")
_DISCONNECT = re.compile(r"Disconnected from server|Client disconnected|Lost connection to server|Connection lost")
_STOPPING = re.compile(r"\]: Stopping!\s*$")


def apply_line(state: LogState, line: str) -> bool:
    """Update ``state`` from one log line; return True if anything changed."""
    before = (state.username, state.connection, state.clean_shutdown)
    if m := _USER.search(line):
        state.username = m.group(1)
    elif m := _CONNECT.search(line):
        host, port = m.group(1), m.group(2)
        state.connection = f"Server: {host}" + ("" if port == "25565" else f":{port}")
    elif _SINGLEPLAYER.search(line):
        state.connection = "Singleplayer"
    elif _DISCONNECT.search(line):
        state.connection = "Disconnected"
    elif _LEFT_WORLD.search(line):
        state.connection = "In menus"
    elif _STOPPING.search(line):
        state.clean_shutdown = True
    return before != (state.username, state.connection, state.clean_shutdown)


class LogTail:
    """Follows a log file across appends, truncation and replacement."""

    MAX_INITIAL_BYTES = 32 * 1024 * 1024

    def __init__(self, path: Path, not_before: float | None = None):
        self.path = path
        self.not_before = not_before  # ignore a file last written before this time (previous session)
        self._inode: int | None = None
        self._offset = 0
        self._partial = b""

    def read_new_lines(self) -> tuple[list[str], bool]:
        """Return (new complete lines, reset) where reset means the file was replaced."""
        try:
            st = os.stat(self.path)
        except OSError:
            return [], False
        if self.not_before is not None and st.st_mtime < self.not_before - 2:
            return [], False
        reset = False
        if self._inode != st.st_ino or st.st_size < self._offset:
            reset = self._inode is not None
            self._inode = st.st_ino
            self._offset = max(0, st.st_size - self.MAX_INITIAL_BYTES) if not reset else 0
            self._partial = b""
        if st.st_size == self._offset:
            return [], reset
        try:
            with open(self.path, "rb") as fh:
                fh.seek(self._offset)
                data = fh.read(st.st_size - self._offset)
        except OSError:
            return [], reset
        self._offset += len(data)
        data = self._partial + data
        lines = data.split(b"\n")
        self._partial = lines.pop()
        return [ln.decode("utf-8", errors="replace").rstrip("\r") for ln in lines], reset
