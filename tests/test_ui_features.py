"""UI-level tests for toggles, settings dialog, multi-selection and bulk actions (offscreen Qt, mock backends)."""

from __future__ import annotations

import json

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from mcmanager.config import AppSettings, DashboardEntry, SettingsStore
from mcmanager.notifications import InAppBackend, Notifier
from mcmanager.prism import PrismInstallation
from mcmanager.runtime.manager import InstanceManager
from mcmanager.runtime.models import RunState
from mcmanager.ui.selection import Selection
from tests.test_manager import FakeLauncher, FakeWindows, gp, poll

CMD = Qt.KeyboardModifier.ControlModifier  # ⌘ on macOS, Ctrl elsewhere (Qt's mapping)
SHIFT = Qt.KeyboardModifier.ShiftModifier
NONE = Qt.KeyboardModifier.NoModifier
NAMES = ["A", "B", "C", "D", "E"]


@pytest.fixture
def ui(qapp, tmp_path):
    from mcmanager.runtime.previews import PreviewCoordinator
    from mcmanager.ui.main_window import MainWindow
    from mcmanager.ui.theme import apply_theme
    from mcmanager.windows.capture import CaptureWorker

    apply_theme(qapp)
    for name in NAMES:
        d = tmp_path / "instances" / name
        (d / "minecraft").mkdir(parents=True)
        (d / "instance.cfg").write_text(f"[General]\nname={name}\n")
    (tmp_path / "accounts.json").write_text(json.dumps({"accounts": []}))
    install = PrismInstallation(executable=None, app_bundle=None, data_dir=tmp_path, instances_dir=tmp_path / "instances")
    settings = AppSettings(entries=[DashboardEntry(display_name=n, prism_instance_id=n) for n in NAMES])
    launcher = FakeLauncher()
    m = InstanceManager(settings, SettingsStore(tmp_path / "s.json"), install, launcher, FakeWindows())
    w = MainWindow(m, PreviewCoordinator(m, CaptureWorker(None)), mock=True)
    w.resize(1400, 900)
    w.show()
    qapp.processEvents()
    w.rebuild_grid()
    yield w, m, launcher
    w.close()
    m.shutdown()


def ids_of(m, names):
    by_name = {e.display_name: e.id for e in m.entries}
    return [by_name[n] for n in names]


def selected_names(w, m):
    return [m.entry(i).display_name for i in w.selection.ordered(w._display_order)]


def click(w, m, name, mods=NONE):
    card = w.cards[ids_of(m, [name])[0]]
    QTest.mouseClick(card.preview, Qt.MouseButton.LeftButton, mods, QPoint(30, 30))


# ------------------------------------------------------------------ toggles
def test_toggle_shows_blue_highlight_and_check_only_when_on(qapp):
    from mcmanager.ui.theme import apply_theme
    from mcmanager.ui.widgets import toggle

    apply_theme(qapp)
    on, off = toggle("Staggered Launch", True), toggle("Staggered Launch", False)
    for box in (on, off):
        box.resize(260, 34)
        box.show()
    qapp.processEvents()

    def sample(box):
        img = box.grab().toImage()
        ratio = img.devicePixelRatio()
        background = img.pixelColor(int(220 * ratio), int(17 * ratio))
        # the indicator sits at the left, after the 10 px padding (sample generously around it)
        indicator = [img.pixelColor(int(x * ratio), int(y * ratio)) for x in range(6, 34) for y in range(4, 30)]
        return background, indicator

    bg_on, ind_on = sample(on)
    bg_off, ind_off = sample(off)
    assert bg_on.blue() > bg_on.red() + 20 and bg_on != bg_off  # blue-tinted row when on
    assert any(c.red() > 200 and c.green() > 200 and c.blue() > 200 for c in ind_on)  # white check mark
    assert not any(c.red() > 200 and c.green() > 200 and c.blue() > 200 for c in ind_off)  # no check when off
    on.setChecked(False)
    qapp.processEvents()
    assert sample(on)[0] == bg_off  # back to the normal look


def test_settings_dialog_round_trip_and_restart(ui, tmp_path):
    from mcmanager.ui.dialogs import SettingsDialog

    w, m, _ = ui
    dlg = SettingsDialog(m, w, notifier=Notifier(m.settings.notifications, InAppBackend(lambda *a: None)))
    assert not dlg.staggered.isChecked() and not dlg.delay.isEnabled()
    assert "Only applies" in dlg.delay_note.text()
    dlg.staggered.setChecked(True)
    assert dlg.delay.isEnabled() and dlg.delay_note.text() == ""
    dlg.delay.setValue(-10)  # spin box refuses invalid values
    assert dlg.delay.value() == 1
    dlg.delay.setValue(45)
    dlg.notify_toggles["started"].setChecked(True)
    dlg.notify_toggles["crashed"].setChecked(False)
    assert not dlg.notify_toggles["disconnected"].isEnabled()  # unavailable category
    dlg.notify_master.setChecked(False)
    assert not any(b.isEnabled() for b in dlg.notify_toggles.values())
    dlg._accept()
    m.save()

    reloaded = SettingsStore(tmp_path / "s.json").load()  # "restart"
    assert reloaded.staggered_launch and reloaded.launch_delay_s == 45
    n = reloaded.notifications
    assert (n.enabled, n.started, n.crashed, n.launch_failed) == (False, True, False, True)
    m.settings = reloaded
    dlg2 = SettingsDialog(m, w)
    assert dlg2.staggered.isChecked() and dlg2.delay.value() == 45 and not dlg2.notify_master.isChecked()
    assert dlg2.notify_toggles["started"].isChecked() and not dlg2.notify_toggles["crashed"].isChecked()


# ---------------------------------------------------------------- selection
def test_selection_rules():
    order = list("abcde")
    s = Selection()
    s.click("b", NONE, order)
    s.click("d", SHIFT, order)
    assert s.ordered(order) == ["b", "c", "d"]
    s.click("a", SHIFT, order)  # range in the other direction re-sizes from the same anchor
    assert s.ordered(order) == ["a", "b"]
    s.click("e", CMD, order)  # toggle adds without clearing
    assert s.ordered(order) == ["a", "b", "e"]
    s.click("b", CMD, order)  # toggle removes
    assert s.ordered(order) == ["a", "e"]
    s.click("d", CMD | SHIFT, order)  # anchor is b (last clicked): adds b..d to the existing selection
    assert s.ordered(order) == ["a", "b", "c", "d", "e"]
    s.click("c", NONE, order)
    assert s.ordered(order) == ["c"]


def test_clicks_on_tiles_select_without_side_effects(ui):
    w, m, launcher = ui
    click(w, m, "B")
    assert selected_names(w, m) == ["B"] and w.bulk_bar.isVisibleTo(w)
    click(w, m, "D", SHIFT)
    assert selected_names(w, m) == ["B", "C", "D"]
    click(w, m, "A", CMD)
    assert selected_names(w, m) == ["A", "B", "C", "D"]
    click(w, m, "C", CMD)
    assert selected_names(w, m) == ["A", "B", "D"]
    assert all(w.cards[i].check.isVisibleTo(w.cards[i]) == (i in w.selection) for i in w.cards)
    assert launcher.launched == [] and w.stack.currentWidget() is not w.detail  # nothing launched/expanded
    assert all(m.info(e.id).state == RunState.STOPPED for e in m.entries)
    click(w, m, "E")
    assert selected_names(w, m) == ["E"]
    w._escape()
    assert selected_names(w, m) == [] and not w.bulk_bar.isVisibleTo(w)


def test_selection_follows_groups_and_reordering(ui, qapp):
    w, m, _ = ui
    m.add_group("G")
    m.move_entries(ids_of(m, ["D", "E"]), "G")
    qapp.processEvents()
    assert [m.entry(i).display_name for i in w._display_order] == ["D", "E", "A", "B", "C"]
    click(w, m, "E")
    click(w, m, "B", SHIFT)  # range across the group boundary, in display order
    assert selected_names(w, m) == ["E", "A", "B"]
    # dragging a selected tile carries the whole selection
    assert [m.entry(i).display_name for i in w._drag_ids(ids_of(m, ["A"])[0])] == ["E", "A", "B"]
    assert w._drag_ids(ids_of(m, ["C"])[0]) == ids_of(m, ["C"])
    w._on_drop(w._drag_ids(ids_of(m, ["A"])[0]), "G", ids_of(m, ["D"])[0])
    qapp.processEvents()
    assert [e.display_name for e in m.group_entries("G")] == ["E", "A", "B", "D"]
    assert selected_names(w, m) == ["E", "A", "B"]  # selection survives the move


def test_plain_press_on_selected_tile_keeps_selection_for_drag(ui, monkeypatch):
    from mcmanager.ui import card as card_module

    w, m, _ = ui
    click(w, m, "A")
    click(w, m, "C", SHIFT)
    carried = []
    monkeypatch.setattr(card_module.QDrag, "exec", lambda self, *a: carried.append(self.mimeData()) or a[0])
    card = w.cards[ids_of(m, ["B"])[0]]
    QTest.mousePress(card.preview, Qt.MouseButton.LeftButton, NONE, QPoint(30, 30))
    QTest.qWait(200)  # hold the button briefly, as a real drag does
    QTest.mouseMove(card.preview, QPoint(80, 70))
    QTest.mouseRelease(card.preview, Qt.MouseButton.LeftButton, NONE, QPoint(80, 70))
    from mcmanager.ui.groups import entry_ids_from
    assert [m.entry(i).display_name for i in entry_ids_from(carried[0])] == ["A", "B", "C"]
    assert selected_names(w, m) == ["A", "B", "C"]
    click(w, m, "B")  # a plain click without dragging then selects just that tile
    assert selected_names(w, m) == ["B"]


# --------------------------------------------------------------- bulk bar
def test_bulk_launch_and_stop_only_selected(ui, monkeypatch):
    w, m, launcher = ui
    click(w, m, "B")
    click(w, m, "D", CMD)
    assert w.bulk_bar.title.text() == "2 selected" and w.bulk_bar.launch_btn.isEnabled()
    assert not w.bulk_bar.stop_btn.isEnabled()
    w.bulk_action("launch")
    assert [r.instance_id for r in launcher.launched] == ["B"]  # D waits in the queue
    assert m.queue.is_waiting(ids_of(m, ["D"])[0]) and w.queue_bar.isVisibleTo(w)

    poll(m, {"B": gp(m, "B", pid=2), "C": gp(m, "C", pid=3)})  # B started; C runs but isn't selected
    stopped = []
    monkeypatch.setattr(m, "stop", lambda eid: stopped.append(m.entry(eid).display_name))
    monkeypatch.setattr(w, "_confirm", lambda title, text: "“B”" in text and "“C”" not in text)
    w.bulk_bar.update_for(m, w.selection.ordered(w._display_order))
    assert w.bulk_bar.stop_btn.isEnabled()
    w.bulk_action("stop")
    assert stopped == ["B"]


def test_queue_cancel_from_bar_and_card(ui):
    w, m, launcher = ui
    m.settings.staggered_launch = True
    m.launch_many(ids_of(m, ["A", "B", "C"]))
    b = ids_of(m, ["B"])[0]
    card = w.cards[b]
    assert card.primary.text() == "Cancel Queued Launch"
    card.primary.click()
    assert not m.queue.is_waiting(b)
    w._cancel_queue()
    assert m.queue.waiting() == [] and [r.instance_id for r in launcher.launched] == ["A"]


def test_header_shows_combined_playtime(ui):
    w, m, _ = ui
    a, b = ids_of(m, ["A", "B"])
    m.playtime.accounts.clear()
    m.playtime.observe(a, 1, m.playtime.clock() - 3600)
    m.playtime.observe(b, 2, m.playtime.clock() - 1800)
    w._tick()
    assert w.playtime_label.text() == "1h 30m played in total"


def test_expanding_a_selected_tile_keeps_the_selection(ui):
    w, m, _ = ui
    click(w, m, "A")
    click(w, m, "B", CMD)
    assert w.bulk_bar.isVisibleTo(w)
    w.expand(ids_of(m, ["B"])[0])
    assert not w.bulk_bar.isVisibleTo(w)  # toolbar hidden while an account is expanded…
    w.refresh_entry(ids_of(m, ["A"])[0])  # …and stays hidden when tiles update in the background
    assert not w.bulk_bar.isVisibleTo(w)
    w.show_grid()
    assert w.bulk_bar.isVisibleTo(w) and w.bulk_bar.title.text() == "2 selected"  # back with the grid
    assert selected_names(w, m) == ["A", "B"]
    w.expand(ids_of(m, ["D"])[0])  # expanding an unselected tile doesn't select it either
    assert selected_names(w, m) == ["A", "B"] and not w.detail.selected_tag.isVisibleTo(w.detail)
    w.show_grid()
    w.expand(ids_of(m, ["A"])[0])
    assert w.detail.selected_tag.isVisibleTo(w.detail)


def test_bulk_bar_texts_are_not_clipped(ui, qapp):
    w, m, _ = ui
    poll(m, {"A": gp(m, "A", pid=1), "B": gp(m, "B", pid=2)})
    click(w, m, "A")
    click(w, m, "B", CMD)  # no processEvents(): sizes must be right immediately
    bar = w.bulk_bar
    assert bar.title.text() == "2 selected" and bar.stop_btn.text() == "Stop Selected (2)"
    for widget in (bar.title, bar.detail, bar.launch_btn, bar.stop_btn, bar.restart_btn, bar.clear_btn):
        assert widget.width() >= widget.sizeHint().width(), widget


@pytest.mark.parametrize("width", [700, 1040, 1280])  # 700: narrower than the header allows
def test_header_items_do_not_overlap(ui, qapp, width):
    w, m, _ = ui
    w.resize(width, 800)
    qapp.processEvents()
    header = w.stat_label.parentWidget()
    items = [c for c in header.children() if hasattr(c, "isVisible") and c.isVisible() and hasattr(c, "geometry")]
    rects = sorted((c.geometry() for c in items if c.geometry().width() > 0), key=lambda r: r.left())
    for a, b in zip(rects, rects[1:]):
        if a.top() < b.bottom() and b.top() < a.bottom():  # same row
            assert a.right() < b.left(), (a, b)
    from PySide6.QtWidgets import QPushButton
    for b in header.findChildren(QPushButton):
        if b.isVisible():
            assert b.width() >= b.sizeHint().width(), b.text()  # no clipped button text


def test_clicking_empty_space_clears_selection(ui, qapp):
    w, m, launcher = ui
    m.add_group("G")
    m.move_entries(ids_of(m, ["A"]), "G")
    qapp.processEvents()

    def select_two():
        click(w, m, "B")
        click(w, m, "C", CMD)
        assert selected_names(w, m) == ["B", "C"]

    select_two()
    host = w.grid_host
    QTest.mouseClick(host, Qt.MouseButton.LeftButton, NONE, QPoint(host.width() - 5, host.height() - 5))
    assert selected_names(w, m) == [] and not w.bulk_bar.isVisibleTo(w)

    select_two()  # empty space inside a group section (right of its only tile) counts too
    section = next(s for s in w.sections if s.group == "G")
    QTest.mouseClick(section, Qt.MouseButton.LeftButton, NONE, QPoint(section.width() - 20, section.height() // 2))
    assert selected_names(w, m) == []

    select_two()  # clicks on tiles, tile buttons, or with ⌘/Shift held don't clear it
    QTest.mouseClick(host, Qt.MouseButton.LeftButton, CMD, QPoint(host.width() - 5, host.height() - 5))
    QTest.mouseClick(w.cards[ids_of(m, ["B"])[0]].expand_btn, Qt.MouseButton.LeftButton)
    w.show_grid()
    assert selected_names(w, m) == ["B", "C"] and launcher.launched == []
    w.bulk_bar.clear_btn.click()  # Deselect All still works
    assert selected_names(w, m) == []


def test_small_or_quick_movements_do_not_start_a_drag(ui, monkeypatch):
    from mcmanager.ui import card as card_module

    w, m, _ = ui
    drags = []
    monkeypatch.setattr(card_module.QDrag, "exec", lambda self, *a: drags.append(1) or a[0])
    preview = w.cards[ids_of(m, ["B"])[0]].preview

    # wobble of a few pixels while clicking, even when held: just a click
    QTest.mousePress(preview, Qt.MouseButton.LeftButton, NONE, QPoint(40, 40))
    QTest.qWait(200)
    QTest.mouseMove(preview, QPoint(48, 46))
    QTest.mouseRelease(preview, Qt.MouseButton.LeftButton, NONE, QPoint(48, 46))
    assert drags == [] and selected_names(w, m) == ["B"]

    # quick flick right after pressing: still a click
    QTest.mousePress(preview, Qt.MouseButton.LeftButton, NONE, QPoint(40, 40))
    QTest.mouseMove(preview, QPoint(90, 70))
    QTest.mouseRelease(preview, Qt.MouseButton.LeftButton, NONE, QPoint(90, 70))
    assert drags == []

    # held, then moved clearly: a drag
    QTest.mousePress(preview, Qt.MouseButton.LeftButton, NONE, QPoint(40, 40))
    QTest.qWait(200)
    QTest.mouseMove(preview, QPoint(90, 70))
    QTest.mouseRelease(preview, Qt.MouseButton.LeftButton, NONE, QPoint(90, 70))
    assert drags == [1]


@pytest.mark.parametrize("state,expected,request_shown,open_shown", [
    ("allowed", "✅ Allowed", False, False),
    ("denied", "⚠️ Turned off", False, True),
    ("not determined", "⚠️ Not asked yet", True, True),
    ("unknown", "❔ Can't be checked", False, True),
    ("unavailable", "❔ Native notifications aren't available", False, False),
])
def test_permissions_tab_shows_notification_permission(ui, state, expected, request_shown, open_shown):
    from mcmanager.notifications import NotificationBackend
    from mcmanager.ui.dialogs import SettingsDialog

    w, m, _ = ui

    class FakeBackend(NotificationBackend):
        name = "fake"
        requested = 0

        def send(self, title, message):
            return True

        def request_permission(self):
            FakeBackend.requested += 1
            self.permission = "allowed"

    backend = FakeBackend()
    backend.permission = state
    dlg = SettingsDialog(m, w, notifier=Notifier(m.settings.notifications, backend))
    assert dlg.notify_perm_label.text().startswith(expected)
    assert (not dlg.notify_perm_request.isHidden()) == request_shown  # (its tab isn't the current one)
    assert (not dlg.notify_perm_open.isHidden()) == open_shown
    if request_shown:
        dlg.notify_perm_request.click()
        dlg._update_notify_status()  # what the dialog's timer does twice a second
        assert FakeBackend.requested == 1 and dlg.notify_perm_label.text().startswith("✅ Allowed")


def test_tile_buttons_do_not_change_selection(ui):
    w, m, launcher = ui
    b, c = (w.cards[i] for i in ids_of(m, ["B", "C"]))
    b.primary.click()  # Launch on an unselected tile
    assert [r.instance_id for r in launcher.launched] == ["B"] and selected_names(w, m) == []
    poll(m, {"B": gp(m, "B", pid=2)})
    b.focus_btn.click()  # Bring to Front
    assert selected_names(w, m) == []

    click(w, m, "C")  # an existing selection is left exactly as it is
    b.focus_btn.click()
    c.primary.click()
    assert selected_names(w, m) == ["C"] and [r.instance_id for r in launcher.launched] == ["B", "C"]


def test_no_tile_button_selects_the_tile(ui, monkeypatch):
    from PySide6.QtWidgets import QPushButton

    from mcmanager.ui import card as card_module

    w, m, _ = ui
    card = w.cards[ids_of(m, ["B"])[0]]
    poll(m, {"B": gp(m, "B", pid=2)})  # running, so every button is enabled
    menus = []

    class FakeMenu:  # the real ⋯ menu would block in exec()
        def exec(self, *a):
            menus.append(1)

    monkeypatch.setattr(card_module, "build_entry_menu", lambda *a, **k: FakeMenu())
    for b in card.findChildren(QPushButton):
        assert b.isEnabled(), b.text()
        QTest.mouseClick(b, Qt.MouseButton.LeftButton)
        w.show_grid()
        assert selected_names(w, m) == [], f"{b.text()!r} selected the tile"
    assert menus == [1]  # the ⋯ button was among them


def test_feedback_menu_offers_github_and_discord(ui, monkeypatch):
    from PySide6.QtWidgets import QApplication, QLabel

    from mcmanager import ISSUES_URL
    from mcmanager.ui import main_window

    w, m, _ = ui
    menu = w._build_feedback_menu(w)
    text = menu.actions()[0].defaultWidget().text()
    assert "GitHub" in text and "Discord" in text and "nicolbolas123" in text
    opened = []
    monkeypatch.setattr(main_window.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    by_text = {a.text(): a for a in menu.actions()}
    by_text["Open an Issue on GitHub"].trigger()
    assert opened == [ISSUES_URL] and ISSUES_URL.startswith("https://github.com/nicolbolas123/")
    by_text["Copy Discord username"].trigger()
    assert QApplication.clipboard().text() == "nicolbolas123"
