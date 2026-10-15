"""Shared fixtures. Qt runs without a real display so tests never open windows on the desktop.

On Windows the "minimal" platform is used instead of "offscreen": both show nothing, but only
"minimal" uses the Windows font database, so text is measured with real fonts (offscreen draws
every glyph as a 1-em box there, which breaks layout tests).
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "minimal" if sys.platform == "win32" else "offscreen")

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])
