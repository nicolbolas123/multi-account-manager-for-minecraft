"""State-machine tests for InstanceManager using synthetic poll results."""

from __future__ import annotations

import json
import time

import pytest

from mcmanager.config import AppSettings, DashboardEntry, SettingsStore
from mcmanager.prism import PrismInstallation
from mcmanager.runtime.manager import InstanceManager
from mcmanager.runtime.models import LogState, RunState
from mcmanager.runtime.poller import PollResult
from mcmanager.runtime.process_finder import GameProcess
from mcmanager.windows.discovery import WindowInfo
from mcmanager.windows.focus import ActionResult


class FakeLauncher:
    is_mock = True

    def __init__(self):
        self.launched = []

    def launch(self, install, request):
        self.launched.append(request)

        class P:
            def poll(self):
                return None
        return P()

    def show(self, install, instance_id):
        pass


class FakeWindows:
    is_mock = True

    def find_windows(self, pids):
        return {}

    def bring_to_front(self, pid):
        return ActionResult(True)

    def close_gracefully(self, pid):
        return ActionResult(False, "n/a")


@pytest.fixture
def env(tmp_path):
    for name in ("Inst1", "Inst2"):
        d = tmp_path / "instances" / name
        (d / "minecraft").mkdir(parents=True)
        (d / "instance.cfg").write_text(f"[General]\nname={name}\n")
    (tmp_path / "accounts.json").write_text(json.dumps({"accounts": []}))
    install = PrismInstallation(executable=None, app_bundle=None, data_dir=tmp_path, instances_dir=tmp_path / "instances")
    settings = AppSettings(entries=[
        DashboardEntry(display_name="Main", prism_instance_id="Inst1"),
        DashboardEntry(display_name="Alt", prism_instance_id="Inst1"),
        DashboardEntry(display_name="Bot", prism_instance_id="Inst2"),
    ])
    launcher = FakeLauncher()
    m = InstanceManager(settings, SettingsStore(tmp_path / "s.json"), install, launcher, FakeWindows())
    return m, launcher, tmp_path


def poll(m, procs: dict, windows=None, logs=None):
    result = PollResult(timestamp=time.time())
    for inst in m.instances.values():
        result.processes[str(inst.path)] = procs.get(inst.id)
    result.windows = windows or {}
    result.logs = logs or {}
    m._on_poll(result)


def gp(m, inst_id, pid=4242, t=None):
    return GameProcess(pid, t if t is not None else time.time(), str(m.instances[inst_id].path))


def test_launch_then_running_then_unexpected_exit(env):
    m, launcher, _ = env
    main, alt, _bot = m.entries
    assert m.launch(main.id)
    assert m.info(main.id).state == RunState.LAUNCHING
    assert launcher.launched[0].instance_id == "Inst1"

    # duplicate launches are refused, including via another entry on the same instance
    assert not m.launch(main.id)
    assert not m.launch(alt.id)

    proc = gp(m, "Inst1")
    win = WindowInfo(window_id=7, pid=proc.pid, width=854, height=480)
    key = str(m.instances["Inst1"].path)
    poll(m, {"Inst1": proc}, {proc.pid: win}, {key: LogState(username="Steve", connection="Server: x")})
    info = m.info(main.id)
    assert info.state == RunState.RUNNING and info.pid == proc.pid and not info.attached
    assert info.window == win and info.log.username == "Steve"
    assert m.info(alt.id).state == RunState.STOPPED  # the process belongs to the launching entry only

    poll(m, {})
    assert info.state == RunState.EXITED and info.pid is None
    assert info.log.connection is None and info.log.username == "Steve"


def test_attach_to_already_running_and_crash_report(env):
    m, _, tmp = env
    _main, _alt, bot = m.entries
    started = time.time() - 5
    poll(m, {"Inst2": gp(m, "Inst2", pid=99, t=started)})
    info = m.info(bot.id)
    assert info.state == RunState.RUNNING and info.attached

    reports = tmp / "instances" / "Inst2" / "minecraft" / "crash-reports"
    reports.mkdir()
    (reports / "crash-2026-01-01_00.00.00-client.txt").write_text("boom")
    poll(m, {})
    assert info.state == RunState.CRASHED and info.crash_report.endswith("client.txt")


def test_requested_stop_and_clean_exit(env):
    m, _, _ = env
    main = m.entries[0]
    poll(m, {"Inst1": gp(m, "Inst1", pid=1)})
    info = m.info(main.id)
    info.stop_requested_at = time.time()  # what stop() records before signalling
    info.state = RunState.STOPPING
    poll(m, {})
    assert info.state == RunState.STOPPED

    poll(m, {"Inst1": gp(m, "Inst1", pid=2)})
    key = str(m.instances["Inst1"].path)
    poll(m, {}, logs={key: LogState(clean_shutdown=True)})
    assert info.state == RunState.EXITED and "inside the game" in info.message


def test_launch_timeout(env):
    m, _, _ = env
    main = m.entries[0]
    m.settings.launch_timeout_s = 30
    m.launch(main.id)
    m.info(main.id).launch_requested_at = time.time() - 31
    poll(m, {})
    assert m.info(main.id).state == RunState.FAILED


def test_missing_instance_cannot_launch(env):
    m, _, _ = env
    entry = DashboardEntry(display_name="Ghost", prism_instance_id="DoesNotExist")
    m.add_entry(entry)
    ok, reason = m.can_launch(entry.id)
    assert not ok and "not found" in reason


def test_cgimage_conversion_scales():
    pytest.importorskip("Quartz")
    import Quartz

    from mcmanager.windows.capture import cgimage_to_qimage

    w, h = 400, 200
    buf = bytearray(b"\x00\x00\xff\xff" * (w * h))  # BGRA red
    cs = Quartz.CGColorSpaceCreateWithName(Quartz.kCGColorSpaceSRGB)
    ctx = Quartz.CGBitmapContextCreate(buf, w, h, 8, w * 4, cs,
                                       Quartz.kCGImageAlphaPremultipliedFirst | Quartz.kCGBitmapByteOrder32Little)
    img = cgimage_to_qimage(Quartz.CGBitmapContextCreateImage(ctx), 100)
    assert (img.width(), img.height()) == (100, 50)
    assert img.pixel(50, 25) == 0xFFFF0000


# -------------------------------------------------------------------- groups
def names(entries):
    return [e.display_name for e in entries]


def test_groups_create_rename_delete_persist(env, tmp_path):
    m, _, _ = env
    main, alt, bot = m.entries
    assert m.add_group("Farm") == "Farm"
    assert m.add_group("Farm") == "Farm 2"  # names stay unique
    m.move_entry(bot.id, "Farm")
    m.move_entry(main.id, "Farm", before_id=bot.id)
    assert names(m.group_entries("Farm")) == ["Main", "Bot"]
    assert names(m.group_entries("")) == ["Alt"]

    assert not m.rename_group("Farm", "Farm 2")  # taken
    assert m.rename_group("Farm", "Bots")
    assert main.group == bot.group == "Bots"

    reloaded = SettingsStore(tmp_path / "s.json").load()
    assert [g.name for g in reloaded.groups] == ["Farm 2", "Bots"]  # newest group first
    assert [(e.display_name, e.group) for e in reloaded.entries] == [("Alt", ""), ("Main", "Bots"), ("Bot", "Bots")]

    m.remove_group("Bots")  # entries are kept, just ungrouped
    assert [g.name for g in m.groups] == ["Farm 2"]
    assert len(m.entries) == 3 and all(e.group == "" for e in m.entries)


def test_move_entry_reorders_within_group(env):
    m, _, _ = env
    main, alt, bot = m.entries
    m.move_entry(bot.id, "", before_id=main.id)
    assert names(m.entries) == ["Bot", "Main", "Alt"]
    m.move_entry(bot.id, "")  # no before → end of group
    assert names(m.entries) == ["Main", "Alt", "Bot"]
    m.move_entry(alt.id, "", before_id=alt.id)  # dropped on itself: no change
    assert names(m.entries) == ["Main", "Alt", "Bot"]
    m.move_entry(alt.id, "Missing")  # unknown group is refused
    assert alt.group == ""


def test_group_typed_in_dialog_is_created(env):
    m, _, _ = env
    entry = m.entries[0]
    entry.group = " Typed "
    m.update_entry(entry)
    assert entry.group == "Typed" and [g.name for g in m.groups] == ["Typed"]


def group_names(m):
    return [g.name for g in m.groups]


def test_new_groups_start_at_top_below_pinned(env):
    m, _, _ = env
    for name in ("A", "B", "C"):
        m.add_group(name)
    assert group_names(m) == ["C", "B", "A"]
    m.set_group_pinned("A", True)
    assert group_names(m) == ["A", "C", "B"]
    m.add_group("D")
    assert group_names(m) == ["A", "D", "C", "B"]  # top, but under the pinned group


def test_pinned_groups_stay_on_top_when_moving(env, tmp_path):
    m, _, _ = env
    for name in ("A", "B", "C", "D"):
        m.add_group(name)  # → D C B A
    m.set_group_pinned("B", True)
    m.set_group_pinned("A", True)
    assert group_names(m) == ["B", "A", "D", "C"]

    assert not m.can_move_group("D", -1)  # would cross into the pinned block
    m.move_group("D", -1)
    assert group_names(m) == ["B", "A", "D", "C"]
    m.move_group("D", 1)
    assert group_names(m) == ["B", "A", "C", "D"]
    m.move_group("A", -1)
    assert group_names(m) == ["A", "B", "C", "D"]
    assert not m.can_move_group("B", 1)
    m.move_group("D", -99)  # "Move to Top" stops below the pinned groups
    assert group_names(m) == ["A", "B", "D", "C"]

    m.set_group_pinned("A", False)  # unpinned groups land at the top of the unpinned block
    assert group_names(m) == ["B", "A", "D", "C"]
    reloaded = SettingsStore(tmp_path / "s.json").load()
    assert [(g.name, g.pinned) for g in reloaded.groups] == [("B", True), ("A", False), ("D", False), ("C", False)]


def test_favorites_first_setting(env, tmp_path):
    m, _, _ = env
    main, alt, bot = m.entries
    m.add_group("G")
    for e in (main, alt, bot):
        m.move_entry(e.id, "G")
    bot.favorite = True
    assert names(m.display_entries("G")) == ["Main", "Alt", "Bot"]  # off: star only, order kept
    m.settings.favorites_first = True
    assert names(m.display_entries("G")) == ["Bot", "Main", "Alt"]
    assert names(m.group_entries("G")) == ["Main", "Alt", "Bot"]  # stored order is untouched
    m.save()
    assert SettingsStore(tmp_path / "s.json").load().favorites_first is True
