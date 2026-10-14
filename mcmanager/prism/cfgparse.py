"""Minimal reader for Prism's Qt-style INI files (``instance.cfg``, ``prismlauncher.cfg``).

QSettings writes ``key=value`` lines, optionally under ``[Section]`` headers,
and quotes values containing special characters. We only need simple string
values, so a tolerant hand-written parser is more reliable than ``configparser``
(which chokes on duplicate keys and Qt's quoting).
"""

from __future__ import annotations

from pathlib import Path


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        inner = value[1:-1]
        return inner.replace('\\"', '"').replace("\\\\", "\\")
    return value


def parse_cfg(text: str) -> dict[str, str]:
    """Return keys from the ``[General]`` section and any section-less keys."""
    result: dict[str, str] = {}
    section = "General"
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith((";", "#")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if "=" not in line or section != "General":
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = _unquote(value)
    return result


def read_cfg(path: Path) -> dict[str, str]:
    try:
        return parse_cfg(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return {}
