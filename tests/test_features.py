"""Play time, launch queue, notifications, bulk operations and new settings (no real games: mocks only)."""

from __future__ import annotations

import json
import time

import pytest

from mcmanager.config import AppSettings, SettingsStore
from mcmanager.config.settings import settings_from_dict
from mcmanager.notifications import InAppBackend, Notifier
from mcmanager.prism import LaunchError
from mcmanager.runtime.launch_queue import DEFAULT_SPACING_S, ItemState
from mcmanager.runtime.models import AppEvent, RunState
from mcmanager.runtime.playtime import PlaytimeTracker, format_duration
from tests.test_manager import env, gp, poll  # noqa: F401  (fixtures)


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


# ------------------------------------------------------------------ playtime
def test_playtime_accumulates_and_survives_restart(tmp_path):
    clock = Clock()
    path = tmp_path / "playtime.json"
    pt = PlaytimeTracker(path, clock)
    pt.note_launch_requested("a")
    pt.observe("a", 100, clock.t)
    clock.advance(600)
    for _ in range(5):  # repeated polls / rediscovery of the same process: one session
        pt.observe("a", 100, clock.t - 600)
    assert pt.current_session_s("a") == 600 and pt.total_s("a") == 600
    clock.advance(300)
    pt.observe("a", None, None, ended="stopped")
    assert pt.total_s("a") == 900 and pt.current_session_s("a") is None
    assert pt.last_session("a").duration == 900 and pt.last_session("a").ended == "stopped"
    clock.advance(3600)  # stopped time is not counted
    assert pt.total_s("a") == 900
    pt.flush()

    pt2 = PlaytimeTracker(path, clock)
    assert pt2.total_s("a") == 900 and len(pt2.accounts["a"].sessions) == 1
    assert pt2.last_launch_requested("a") == pt.last_launch_requested("a")


def test_playtime_independent_accounts_and_combined(tmp_path):
    clock = Clock()
    pt = PlaytimeTracker(tmp_path / "p.json", clock)
    pt.observe("a", 1, clock.t)
    clock.advance(100)
    pt.observe("b", 2, clock.t)  # starts later, runs concurrently
    clock.advance(50)
    assert pt.total_s("a") == 150 and pt.total_s("b") == 50
    assert pt.combined_s(["a", "b"]) == 200  # sum of runtimes, not wall-clock time (150)
    pt.observe("a", None, None)
    clock.advance(1000)
    assert pt.combined_s(["a", "b"]) == 150 + 1050


def test_playtime_dashboard_restart_continues_same_process(tmp_path):
    clock = Clock()
    path = tmp_path / "p.json"
    pt = PlaytimeTracker(path, clock)
    start = clock.t
    pt.observe("a", 42, start)
    clock.advance(120)
    pt.observe("a", 42, start)
    pt.flush(force=True)  # dashboard quits; game keeps running

    clock.advance(500)  # dashboard closed for a while
    pt2 = PlaytimeTracker(path, clock)
    assert pt2.owns("a", 42, start + 0.5)
    pt2.observe("a", 42, start)  # re-attached: same session, not a new one
    clock.advance(10)
    pt2.observe("a", None, None)
    assert pt2.total_s("a") == 630 and len(pt2.accounts["a"].sessions) == 1


def test_playtime_process_ended_while_dashboard_closed_uses_heartbeat(tmp_path):
    clock = Clock()
    path = tmp_path / "p.json"
    pt = PlaytimeTracker(path, clock)
    pt.observe("a", 7, clock.t)
    clock.advance(61)
    pt.observe("a", 7, clock.t - 61)
    pt.flush()  # heartbeat (60 s) saves last_seen
    clock.advance(10_000)  # dashboard and game both gone (e.g. app crashed)

    pt2 = PlaytimeTracker(path, clock)
    pt2.observe("a", None, None)
    s = pt2.last_session("a")
    assert s.estimated and s.duration == 61  # never counts the 10 000 s it can't vouch for
    # a recycled PID with a different start time is a new session, not a continuation
    pt2.observe("a", 7, clock.t)
    assert pt2.accounts["a"].open.start == clock.t


def test_playtime_bad_file_is_backed_up(tmp_path):
    path = tmp_path / "p.json"
    path.write_text("{oops")
    assert PlaytimeTracker(path).accounts == {}
    assert list(tmp_path.glob("p.corrupt-*.json"))


def test_format_duration():
    assert [format_duration(s) for s in (0, 42, 60, 59 * 60, 12 * 3600 + 35 * 60)] == ["0s", "42s", "1m", "59m", "12h 35m"]


def test_manager_records_sessions_per_entry(env):
    m, _, _ = env
    main, _alt, bot = m.entries
    p1, p2 = gp(m, "Inst1", pid=11), gp(m, "Inst2", pid=22)
    poll(m, {"Inst1": p1, "Inst2": p2})
    poll(m, {"Inst1": p1, "Inst2": p2})  # same processes again: no new sessions
    assert m.playtime.accounts[main.id].open.pid == 11 and m.playtime.accounts[bot.id].open.pid == 22
    poll(m, {"Inst2": p2})
    assert len(m.playtime.accounts[main.id].sessions) == 1 and m.playtime.accounts[bot.id].open
    assert m.playtime.accounts[main.id].sessions[0].ended == "exited"


# --------------------------------------------------------------------- queue
@pytest.fixture
def queued(env):
    m, launcher, tmp = env
    clock = Clock(0.0)
    m.queue.clock = clock
    # three entries on three separate instances
    for name in ("Inst3", "Inst4"):
        d = tmp / "instances" / name
        (d / "minecraft").mkdir(parents=True)
        (d / "instance.cfg").write_text(f"[General]\nname={name}\n")
    m.refresh_prism()
    m.entries[1].prism_instance_id = "Inst3"
    from mcmanager.config import DashboardEntry
    m.add_entry(DashboardEntry(display_name="Four", prism_instance_id="Inst4"))
    return m, launcher, clock


def launched(launcher):
    return [r.instance_id for r in launcher.launched]


def test_staggered_launch_in_sequence_respects_delay(queued):
    m, launcher, clock = queued
    m.settings.staggered_launch, m.settings.launch_delay_s = True, 30
    ids = [e.id for e in m.entries]  # Inst1, Inst3, Inst2, Inst4
    assert m.launch_many(ids) == 4
    assert launched(launcher) == ["Inst1"]
    assert m.queue.position(ids[1]) == 1 and m.queue.is_waiting(ids[3])

    poll(m, {"Inst1": gp(m, "Inst1", pid=1)})  # first one is running
    clock.advance(29)
    m.queue.tick()
    assert launched(launcher) == ["Inst1"]  # delay not over yet
    clock.advance(1)
    m.queue.tick()
    assert launched(launcher) == ["Inst1", "Inst3"]

    clock.advance(60)  # delay passed, but the previous launch has no process yet: wait for it
    m.queue.tick()
    assert launched(launcher) == ["Inst1", "Inst3"]
    assert m.queue.blocked_by().entry_id == ids[1]


def test_queue_failure_does_not_block_and_cancel_works(queued, monkeypatch):
    m, launcher, clock = queued
    m.settings.staggered_launch, m.settings.launch_delay_s = True, 10
    ids = [e.id for e in m.entries]
    real = launcher.launch

    def flaky(install, request):
        if request.instance_id == "Inst1":
            raise LaunchError("Prism is broken")
        return real(install, request)

    monkeypatch.setattr(launcher, "launch", flaky)
    m.launch_many(ids)
    # Inst1 failed immediately -> Inst3 is launched right away (no delay was "used")
    assert launched(launcher) == ["Inst3"]
    assert m.queue.item(ids[0]).state == ItemState.FAILED

    # the launch of Inst3 times out -> FAILED -> queue continues after the delay
    m.settings.launch_timeout_s = 5
    m.info(ids[1]).launch_requested_at = time.time() - 6
    poll(m, {})
    clock.advance(10)
    m.queue.tick()
    assert m.queue.item(ids[1]).state == ItemState.FAILED
    assert launched(launcher) == ["Inst3", "Inst2"]

    assert m.queue.cancel_waiting() == 1  # Four had not started yet
    poll(m, {"Inst2": gp(m, "Inst2", pid=5)})
    m.queue.tick()
    clock.advance(100)
    m.queue.tick()
    assert launched(launcher) == ["Inst3", "Inst2"] and not m.queue.active


def test_unstaggered_keeps_original_spacing(queued):
    m, launcher, clock = queued
    m.settings.staggered_launch = False
    m.launch_many([e.id for e in m.entries][:2])
    assert launched(launcher) == ["Inst1"]
    clock.advance(DEFAULT_SPACING_S)
    m.queue.tick()  # no waiting for the first process to appear
    assert launched(launcher) == ["Inst1", "Inst3"]


def test_queue_skips_running_and_duplicates(queued):
    m, launcher, clock = queued
    ids = [e.id for e in m.entries]
    poll(m, {"Inst1": gp(m, "Inst1", pid=1)})  # entry 0 already running
    assert m.launch_many([ids[0], ids[1], ids[1]]) == 1
    assert m.launch_many([ids[1]]) == 0  # already launching
    assert launched(launcher) == ["Inst3"]


def test_direct_launch_takes_item_out_of_queue(queued):
    m, launcher, clock = queued
    m.settings.staggered_launch = True
    ids = [e.id for e in m.entries]
    m.launch_many(ids[:3])
    assert m.queue.is_waiting(ids[2])
    m.launch(ids[2])
    assert m.queue.item(ids[2]).state == ItemState.CANCELLED
    assert launched(launcher) == ["Inst1", "Inst2"]


# ------------------------------------------------------------- bulk actions
def test_bulk_stop_and_launch_only_affect_selection(queued):
    m, launcher, clock = queued
    ids = [e.id for e in m.entries]
    stopped = []
    m.stop = lambda eid: stopped.append(eid)
    poll(m, {"Inst1": gp(m, "Inst1", pid=1), "Inst2": gp(m, "Inst2", pid=2)})
    assert m.stop_many([ids[2]]) == 1 and stopped == [ids[2]]
    m.launch_many([ids[3]])
    assert launched(launcher) == ["Inst4"]


# ------------------------------------------------------------- notifications
def make_notifier(settings):
    shown = []
    return Notifier(settings, InAppBackend(lambda t, msg: shown.append(t))), shown


def test_notification_categories_are_independent_and_deduplicated():
    s = AppSettings().notifications
    n, shown = make_notifier(s)
    crash = AppEvent("crashed", "Main crashed", "x", "exit:main:1", "main")
    assert n.handle(crash) and not n.handle(crash)  # same event twice -> once
    s.launch_failed = False
    assert not n.handle(AppEvent("launch_failed", "Alt failed to launch", "x", "lf:1"))
    s.started = True
    assert n.handle(AppEvent("started", "Alt started", "x", "st:1"))
    s.enabled = False  # master switch
    assert not n.handle(AppEvent("crashed", "Bot crashed", "x", "exit:bot:2"))
    assert shown == ["Main crashed", "Alt started"]


def test_disconnected_category_is_never_sent():
    s = AppSettings().notifications
    s.disconnected = True
    n, shown = make_notifier(s)
    assert not n.handle(AppEvent("disconnected", "x", "y", "d:1"))


def test_bulk_failure_is_one_notification_for_either_category():
    s = AppSettings().notifications
    s.bulk_failed = False  # only "completed" wanted: the failure summary still arrives, once
    n, shown = make_notifier(s)
    ev = AppEvent("bulk_failed", "Bulk launch finished with errors", "1 failed", "bulk:1", also=("bulk_completed",))
    assert n.handle(ev) and not n.handle(ev) and len(shown) == 1


def test_manager_emits_crash_and_launch_failure_events(env, monkeypatch):
    m, launcher, tmp = env
    events = []
    m.app_event.connect(events.append)
    main, _alt, bot = m.entries

    poll(m, {"Inst2": gp(m, "Inst2", pid=9)})  # attached: no "started" event
    reports = tmp / "instances" / "Inst2" / "minecraft" / "crash-reports"
    reports.mkdir()
    (reports / "crash-x-client.txt").write_text("boom")
    poll(m, {})
    poll(m, {})  # later polls don't repeat it
    assert [(e.category, e.title) for e in events] == [("crashed", "Bot crashed")]

    def broken(install, request):
        raise LaunchError("Prism not found")
    monkeypatch.setattr(launcher, "launch", broken)
    m.launch(main.id)
    assert events[-1].category == "launch_failed" and events[-1].title == "Main failed to launch"

    monkeypatch.undo()
    m.launch(main.id)
    poll(m, {"Inst1": gp(m, "Inst1", pid=3)})
    assert events[-1].category == "started" and events[-1].title == "Main started"


def test_bulk_batch_event(queued):
    m, launcher, clock = queued
    events = []
    m.app_event.connect(events.append)
    ids = [e.id for e in m.entries]
    m.launch_many(ids[:2])
    poll(m, {"Inst1": gp(m, "Inst1", pid=1)})
    clock.advance(DEFAULT_SPACING_S)
    m.queue.tick()
    poll(m, {"Inst1": gp(m, "Inst1", pid=1), "Inst3": gp(m, "Inst3", pid=3)})
    m.queue.tick()
    bulk = [e for e in events if e.category.startswith("bulk")]
    assert len(bulk) == 1 and bulk[0].category == "bulk_completed" and "2 started" in bulk[0].message


# ---------------------------------------------------------------- settings
def test_new_settings_persist_and_validate(tmp_path):
    store = SettingsStore(tmp_path / "s.json")
    s = AppSettings()
    s.staggered_launch, s.launch_delay_s = True, 45
    s.notifications.enabled, s.notifications.started = False, True
    store.save(s)
    loaded = store.load()
    assert loaded.staggered_launch and loaded.launch_delay_s == 45
    assert not loaded.notifications.enabled and loaded.notifications.started

    bad = settings_from_dict({"launch_delay_s": -5, "staggered_launch": "yes",
                              "notifications": {"crashed": "no", "future_category": True}})
    assert bad.launch_delay_s == 20 and bad.staggered_launch is False and bad.notifications.crashed is True
    old = settings_from_dict({"tile_width": 400})  # file from before these features
    assert old.tile_width == 400 and old.launch_delay_s == 20 and old.notifications.enabled
    assert settings_from_dict({"notifications": "garbage"}).notifications.enabled


def test_move_entries_keeps_order(env):
    m, _, _ = env
    main, alt, bot = m.entries
    m.add_group("G")
    m.move_entries([bot.id, main.id], "G")
    assert [e.display_name for e in m.group_entries("G")] == ["Bot", "Main"]
    m.move_entries([alt.id, main.id], "G", before_id=bot.id)
    assert [e.display_name for e in m.group_entries("G")] == ["Alt", "Main", "Bot"]
    m.move_entries([alt.id, main.id], "G", before_id=main.id)  # dropped on itself: anchor to next
    assert [e.display_name for e in m.group_entries("G")] == ["Alt", "Main", "Bot"]
