"""Tests for settings, Prism parsing, process matching and log parsing (no GUI)."""

from __future__ import annotations

import json
import plistlib
import subprocess
import sys
import time
from pathlib import Path

import pytest

from mcmanager.config import AppSettings, DashboardEntry, SettingsStore
from mcmanager.prism import LaunchRequest, PrismInstallation, build_launch_command, list_accounts, list_instances
from mcmanager.prism.cfgparse import parse_cfg
from mcmanager.prism.locator import PRISM_BUNDLE_ID, executable_for_bundle, instances_dir_for
from mcmanager.runtime.log_watcher import LogTail, apply_line
from mcmanager.runtime.models import LogState
from mcmanager.runtime.process_finder import GameProcess, process_matches, scan_game_processes, verify_process


# ------------------------------------------------------------------ settings
def test_settings_roundtrip(tmp_path):
    store = SettingsStore(tmp_path / "s.json")
    s = AppSettings()
    s.entries.append(DashboardEntry(display_name="Farm Bot 1", prism_instance_id="inst", account_profile="Alex"))
    s.preview.grid_fps = 7.5
    store.save(s)
    loaded = store.load()
    assert loaded.entries[0].display_name == "Farm Bot 1"
    assert loaded.entries[0].account_profile == "Alex"
    assert loaded.entries[0].id == s.entries[0].id
    assert loaded.preview.grid_fps == 7.5


def test_settings_tolerates_unknown_fields_and_corruption(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"tile_width": 400, "future": 1, "entries": [
        {"display_name": "A", "prism_instance_id": "x", "new_field": True}]}))
    loaded = SettingsStore(path).load()
    assert loaded.tile_width == 400 and loaded.entries[0].display_name == "A"

    path.write_text("{not json")
    assert SettingsStore(path).load().entries == []
    assert list(tmp_path.glob("s.corrupt-*.json")), "corrupt file should be backed up"


# --------------------------------------------------------------------- prism
def test_parse_cfg_handles_quotes_and_sections():
    cfg = parse_cfg('[General]\nname="My \\"Pack\\""\nInstanceType=OneSix\n[Other]\nname=ignored\n')
    assert cfg == {"name": 'My "Pack"', "InstanceType": "OneSix"}


def _make_bundle(root: Path, bundle_id: str) -> Path:
    bundle = root / "Thing.app"
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    (bundle / "Contents" / "MacOS" / "prismlauncher").write_text("#!/bin/sh\n")
    with open(bundle / "Contents" / "Info.plist", "wb") as fh:
        plistlib.dump({"CFBundleIdentifier": bundle_id, "CFBundleExecutable": "prismlauncher"}, fh)
    return bundle


def test_bundle_identifier_is_checked(tmp_path):
    assert executable_for_bundle(_make_bundle(tmp_path / "a", PRISM_BUNDLE_ID)) is not None
    # e.g. GraphPad Prism must never be mistaken for Prism Launcher
    assert executable_for_bundle(_make_bundle(tmp_path / "b", "com.GraphPad.Prism")) is None


def _fake_prism(root: Path) -> Path:
    inst = root / "instances" / "Survival 2"
    (inst / "minecraft").mkdir(parents=True)
    (inst / "instance.cfg").write_text("[General]\nname=Survival Two\n")
    (inst / "mmc-pack.json").write_text(json.dumps({"components": [
        {"uid": "net.minecraft", "version": "1.21.4"}, {"uid": "net.fabricmc.fabric-loader", "version": "0.16"}]}))
    (root / "instances" / "not-an-instance").mkdir()
    (root / "instances" / "instgroups.json").write_text(json.dumps(
        {"formatVersion": "1", "groups": {"Bots": {"instances": ["Survival 2"]}}}))
    (root / "accounts.json").write_text(json.dumps({"accounts": [
        {"type": "MSA", "active": True, "profile": {"name": "Steve", "id": "x"}, "msa": {"token": "SECRET"}}]}))
    return root


def test_list_instances_and_accounts(tmp_path):
    root = _fake_prism(tmp_path)
    assert instances_dir_for(root) == root / "instances"
    instances = list_instances(root / "instances")
    assert [(i.id, i.name, i.minecraft_version, i.loader, i.group) for i in instances] == [
        ("Survival 2", "Survival Two", "1.21.4", "Fabric", "Bots")]
    accounts = list_accounts(root)
    assert [(a.profile_name, a.active) for a in accounts] == [("Steve", True)]
    assert "SECRET" not in repr(accounts)


def test_custom_instance_dir(tmp_path):
    (tmp_path / "prismlauncher.cfg").write_text("[General]\nInstanceDir=/Volumes/Games/mc\n")
    assert instances_dir_for(tmp_path) == Path("/Volumes/Games/mc")


def test_launch_command_uses_documented_flags(tmp_path):
    exe = tmp_path / "prismlauncher"
    exe.write_text("")
    install = PrismInstallation(executable=exe, app_bundle=None, data_dir=tmp_path, instances_dir=tmp_path / "instances")
    assert build_launch_command(install, LaunchRequest("My Inst", profile="Steve")) == [
        str(exe), "--launch", "My Inst", "--profile", "Steve"]
    install.data_dir_overridden = True
    assert build_launch_command(install, LaunchRequest("x"))[:3] == [str(exe), "--dir", str(tmp_path)]


# ----------------------------------------------------------------- processes
def test_process_matches_is_exact_about_instance():
    inst = Path("/data/instances/26.2")
    args = ["java", "-Djava.library.path=/data/instances/26.2/natives", "-cp", "x.jar"]
    assert process_matches(args, None, inst)
    assert not process_matches(args, None, Path("/data/instances/26"))
    assert not process_matches(["java", "-Djava.library.path=/data/instances/26.2 copy/natives"], None, inst)
    assert process_matches(["java"], "/data/instances/26.2/minecraft", inst)
    assert not process_matches(["java"], "/somewhere/else", inst)


def test_scan_finds_only_matching_process(tmp_path):
    inst_a, inst_b = tmp_path / "A", tmp_path / "B"
    for d in (inst_a, inst_b):
        (d / "minecraft").mkdir(parents=True)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)",
                             f"-Djava.library.path={inst_a / 'natives'}"])
    try:
        time.sleep(0.3)
        found = scan_game_processes([inst_a, inst_b])
        assert [p.pid for p in found[str(inst_a)]] == [proc.pid]
        assert found[str(inst_b)] == []
        gp = found[str(inst_a)][0]
        assert verify_process(gp) is not None
        # A wrong create_time (recycled PID) must not verify.
        assert verify_process(GameProcess(gp.pid, gp.create_time - 100, gp.instance_dir)) is None
    finally:
        proc.kill()
        proc.wait()


# ---------------------------------------------------------------------- logs
@pytest.mark.parametrize("line,field,value", [
    ("[17:42:20] [Render thread/INFO]: Setting user: Alex", "username", "Alex"),
    ("[17:42:50] [Render thread/INFO]: Connecting to play.example.net, 25565", "connection", "Server: play.example.net"),
    ("[17:42:50] [Render thread/INFO]: Connecting to 10.0.0.2, 25570", "connection", "Server: 10.0.0.2:25570"),
    ("[10:00:00] [Server thread/INFO]: Starting integrated minecraft server version 1.21", "connection", "Singleplayer"),
    ("[10:00:00] [Render thread/INFO]: Stopping!", "clean_shutdown", True),
])
def test_log_lines(line, field, value):
    state = LogState()
    assert apply_line(state, line)
    assert getattr(state, field) == value


def test_log_tail_follows_appends_and_rotation(tmp_path):
    path = tmp_path / "latest.log"
    path.write_text("a\nb")
    tail = LogTail(path)
    assert tail.read_new_lines() == (["a"], False)
    with open(path, "a") as fh:
        fh.write("c\nd\n")
    assert tail.read_new_lines() == (["bc", "d"], False)
    path.rename(tmp_path / "old.log")
    path.write_text("new\n")
    assert tail.read_new_lines() == (["new"], True)


def test_log_tail_ignores_previous_session(tmp_path):
    path = tmp_path / "latest.log"
    path.write_text("Setting user: Old\n")
    tail = LogTail(path, not_before=time.time() + 60)
    assert tail.read_new_lines() == ([], False)


# -------------------------------------------------------------------- bundle
def test_external_env_strips_bundle_private_variables(monkeypatch):
    from mcmanager.bundle import external_env

    for key in ("QT_PLUGIN_PATH", "QML2_IMPORT_PATH", "_PYI_ARCHIVE_FILE", "PYTHONPATH", "DYLD_LIBRARY_PATH"):
        monkeypatch.setenv(key, "/inside/the/app")
    monkeypatch.setenv("HOME_TEST_KEEP", "1")
    env = external_env()
    assert not any(k in env for k in ("QT_PLUGIN_PATH", "QML2_IMPORT_PATH", "_PYI_ARCHIVE_FILE", "PYTHONPATH",
                                      "DYLD_LIBRARY_PATH"))
    assert env["HOME_TEST_KEEP"] == "1" and env["PATH"]


def test_fake_client_command_source_and_frozen(monkeypatch):
    from mcmanager import bundle

    cmd, env = bundle.fake_client_command()
    assert cmd[1:] == ["-m", "mcmanager.mock.fake_client"]
    assert str(bundle.resource_root()) in env["PYTHONPATH"]  # works from any working directory
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    cmd, env = bundle.fake_client_command()
    assert cmd == [sys.executable, bundle.FAKE_CLIENT_FLAG] and env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


def test_unknown_launch_arguments_are_ignored():
    from mcmanager.app import parse_args

    args = parse_args(["-psn_0_12345", "--mock"])  # Finder may add arguments of its own
    assert args.mock
