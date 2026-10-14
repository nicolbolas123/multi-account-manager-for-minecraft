"""Drag-and-drop between dashboard sections, driven with synthetic Qt drag events."""

from __future__ import annotations

import json
import warnings

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication

from mcmanager.config import AppSettings, DashboardEntry, SettingsStore
from mcmanager.prism import PrismInstallation
from mcmanager.runtime.manager import InstanceManager
from mcmanager.ui.groups import entry_mime, insertion_index
from tests.test_manager import FakeLauncher, FakeWindows


@pytest.fixture
def window(qapp, tmp_path):
    from mcmanager.runtime.previews import PreviewCoordinator
    from mcmanager.ui.main_window import MainWindow
    from mcmanager.windows.capture import CaptureWorker

    d = tmp_path / "instances" / "I"
    (d / "minecraft").mkdir(parents=True)
    (d / "instance.cfg").write_text("[General]\nname=I\n")
    (tmp_path / "accounts.json").write_text(json.dumps({"accounts": []}))
    install = PrismInstallation(executable=None, app_bundle=None, data_dir=tmp_path, instances_dir=tmp_path / "instances")
    settings = AppSettings(entries=[DashboardEntry(display_name=n, prism_instance_id="I") for n in ("A", "B", "C")])
    m = InstanceManager(settings, SettingsStore(tmp_path / "s.json"), install, FakeLauncher(), FakeWindows())
    w = MainWindow(m, PreviewCoordinator(m, CaptureWorker(None)), mock=True)
    w.resize(1300, 900)
    w.show()
    qapp.processEvents()
    yield w, m
    w.close()
    m.shutdown()


def section(w, group):
    return next(s for s in w.sections if s.group == group)


def drop(section, entry_id, pos: QPoint):
    mime, action = entry_mime(entry_id), Qt.DropAction.MoveAction
    buttons, mods = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
    with warnings.catch_warnings():  # QDragEnterEvent only has a (deprecated-marked) QPoint constructor
        warnings.simplefilter("ignore", DeprecationWarning)
        enter = QDragEnterEvent(pos, action, mime, buttons, mods)
    for ev in (enter, QDropEvent(QPointF(pos), action, mime, buttons, mods)):
        QApplication.sendEvent(section, ev)
        assert ev.isAccepted()


def test_insertion_index_reading_order():
    from PySide6.QtCore import QRect
    rects = [QRect(0, 0, 100, 100), QRect(116, 0, 100, 100), QRect(0, 116, 100, 100)]
    assert insertion_index(rects, QPoint(10, 50)) == 0
    assert insertion_index(rects, QPoint(90, 50)) == 1
    assert insertion_index(rects, QPoint(200, 50)) == 2
    assert insertion_index(rects, QPoint(10, 150)) == 2
    assert insertion_index(rects, QPoint(90, 150)) == 3


def test_drag_cards_into_group_and_back(window, qapp):
    w, m = window
    a, b, c = m.entries
    m.add_group("Farm")
    qapp.processEvents()
    assert [s.group for s in w.sections] == ["Farm", ""]  # groups above the ungrouped accounts
    farm = section(w, "Farm")
    assert farm.placeholder.isVisible()

    drop(farm, c.id, farm.placeholder.geometry().center())
    qapp.processEvents()
    assert c.group == "Farm"

    # drop A before C (left half of C's card) inside the group
    farm = section(w, "Farm")
    card_c = w.cards[c.id]
    drop(farm, a.id, card_c.mapTo(farm, QPoint(10, 40)))
    qapp.processEvents()
    assert [e.display_name for e in m.group_entries("Farm")] == ["A", "C"]

    # and back out to the ungrouped section, after B
    ungrouped = section(w, "")
    card_b = w.cards[b.id]
    drop(ungrouped, c.id, card_b.mapTo(ungrouped, QPoint(card_b.width() - 10, 40)))
    qapp.processEvents()
    assert [e.display_name for e in m.group_entries("")] == ["B", "C"]
    assert [e.display_name for e in m.group_entries("Farm")] == ["A"]


def test_collapse_hides_cards(window, qapp):
    w, m = window
    m.add_group("G")
    m.move_entry(m.entries[0].id, "G")
    m.set_group_collapsed("G", True)
    qapp.processEvents()
    g = section(w, "G")
    assert g.collapsed and not g.flow.isVisible() and g.count.text() == "1"


def test_mouse_drag_on_preview_starts_card_drag(window, qapp, monkeypatch):
    from PySide6.QtTest import QTest

    from mcmanager.ui import card as card_module

    w, m = window
    started = []
    monkeypatch.setattr(card_module.QDrag, "exec", lambda self, *a: started.append(self.mimeData()) or a[0])
    card = w.cards[m.entries[1].id]
    preview = card.preview
    QTest.mousePress(preview, Qt.MouseButton.LeftButton, pos=QPoint(40, 40))
    QTest.qWait(200)  # hold the button briefly, as a real drag does
    QTest.mouseMove(preview, QPoint(80, 60))
    QTest.mouseRelease(preview, Qt.MouseButton.LeftButton, pos=QPoint(80, 60))
    assert len(started) == 1
    from mcmanager.ui.groups import entry_id_from
    assert entry_id_from(started[0]) == m.entries[1].id
    assert card.graphicsEffect() is None  # fade removed after the drag


def test_group_menu_pin_and_move(window, qapp):
    w, m = window
    m.add_group("Old")
    m.add_group("New")
    qapp.processEvents()
    assert [s.group for s in w.sections][:2] == ["New", "Old"]

    def act(group, text):
        return next(a for a in w.build_group_menu(group).actions() if a.text() == text)

    assert not act("New", "Move Up").isEnabled() and act("New", "Move Down").isEnabled()
    act("Old", "Pin to Top").trigger()
    qapp.processEvents()
    assert [s.group for s in w.sections][:2] == ["Old", "New"]
    assert section(w, "Old").pin.isVisibleTo(section(w, "Old")) and not section(w, "New").pin.isVisibleTo(section(w, "New"))
    assert not act("New", "Move Up").isEnabled()  # can't move above a pinned group
    act("Old", "Unpin from Top").trigger()
    act("Old", "Move Down").trigger()
    assert [g.name for g in m.groups] == ["New", "Old"]
