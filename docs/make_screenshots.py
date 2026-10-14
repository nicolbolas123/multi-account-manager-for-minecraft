"""Regenerate the README screenshots from mock mode (no real accounts, data or games involved).

    .venv/bin/python docs/make_screenshots.py

Uses a temporary data folder (MCMANAGER_DATA_DIR) and renders offscreen; previews are the
synthetic "MOCK PREVIEW" frames and the fake game processes are stopped afterwards.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "images"
DATA = Path(tempfile.mkdtemp(prefix="mcm-screens-"))
os.environ["MCMANAGER_DATA_DIR"] = str(DATA)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer  # noqa: E402

from mcmanager.app import build, parse_args  # noqa: E402

ctx = build(parse_args(["--mock"]))
app, w, m = ctx.app, ctx.window, ctx.manager
w.resize(1400, 980)
OUT.mkdir(parents=True, exist_ok=True)


def setup():
    m.import_all_instances()
    names = {"Mock Survival": "Main", "Mock Skyblock": "Farm Bot 1", "Mock Creative": "Builder",
             "Mock Crashy": "Farm Bot 2"}
    for e in m.entries:
        e.display_name = names[e.prism_instance_id]
        e.favorite = e.display_name == "Main"
        m.update_entry(e)
    group = m.add_group("Farm Bots")
    m.move_entries([e.id for e in m.entries if e.display_name.startswith("Farm")], group)
    m.launch_many([e.id for e in m.entries if e.display_name != "Builder"])


def shoot(name, widget=None):
    (widget or w).grab().save(str(OUT / name))


def dashboard():
    shoot("dashboard.png")
    main = next(e.id for e in m.entries if e.display_name == "Main")
    w.expand(main)
    QTimer.singleShot(1500, expanded)


def expanded():
    shoot("expanded.png")
    w.show_grid()
    from mcmanager.ui.dialogs import SettingsDialog

    m.settings.staggered_launch = True  # show an enabled setting
    dlg = SettingsDialog(m, w, notifier=ctx.notifier)
    dlg.tabs.setCurrentIndex(1)
    dlg.show()
    QTimer.singleShot(300, lambda: (shoot("settings.png", dlg), dlg.close(), finish()))


def finish():
    for e in m.entries:
        if m.info(e.id).pid:
            m.force_quit(e.id)
    QTimer.singleShot(1500, app.quit)


ctx.worker.start()
m.start()
w.show()
QTimer.singleShot(500, setup)
QTimer.singleShot(9000, dashboard)
app.exec()
m.shutdown()
print("Screenshots written to", OUT)
