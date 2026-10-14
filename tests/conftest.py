"""Shared fixtures. Qt runs offscreen so tests never open windows on the desktop."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])
