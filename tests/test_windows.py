"""Windows support.

Two kinds of tests:
* Windows *rules* simulated on any OS (paths, Prism discovery, process matching with Windows
  path semantics, backend selection, save retries) - these run on macOS too.
* ``@windows_only`` tests that use the real Win32 API (window discovery, capture, focus,
  WM_CLOSE, process control). They run on Windows, e.g. the GitHub Actions Windows runner.
"""

from __future__ import annotations

import importlib
import ntpath
import os
import struct
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="needs real Windows")


# ------------------------------------------------------------------ platform facts and paths
def test_system_wording_follows_platform(monkeypatch):
    import mcmanager.system as system

    try:
        monkeypatch.setattr(sys, "platform", "win32")
        importlib.reload(system)
        assert system.IS_WINDOWS and not system.IS_MAC
        assert system.FILE_MANAGER == "File Explorer" and system.MOD_KEY == "Ctrl+"
        assert not system.needs_privacy_permissions()
        assert "prismlauncher.exe" in system.PRISM_INSTALL_HINT
        monkeypatch.setattr(sys, "platform", "darwin")
        importlib.reload(system)
        assert system.needs_privacy_permissions() and system.MOD_KEY == "⌘"
    finally:
        monkeypatch.undo()
        importlib.reload(system)


def test_windows_app_data_locations(monkeypatch, tmp_path):
    from mcmanager import APP_ID, paths

    monkeypatch.delenv("MCMANAGER_DATA_DIR", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    roaming, local = tmp_path / "App Data" / "Roaming", tmp_path / "App Data" / "Local"
    monkeypatch.setenv("APPDATA", str(roaming))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    assert paths.app_support_dir() == roaming / APP_ID          # settings + play time (roams)
    assert paths.logs_dir() == local / APP_ID / "Logs"
    assert paths.cache_dir() == local / APP_ID / "Cache"
    monkeypatch.delenv("APPDATA")
    assert paths.app_support_dir() == Path.home() / "AppData" / "Roaming" / APP_ID
    # The test override still wins, so tests never touch real data.
    monkeypatch.setenv("MCMANAGER_DATA_DIR", str(tmp_path / "x"))
    assert paths.app_support_dir() == tmp_path / "x" / "support"


def test_machine_names_are_normalized():
    from mcmanager.bundle import normalize_machine

    assert normalize_machine("AMD64") == "x86_64"
    assert normalize_machine("ARM64") == "arm64"
    assert normalize_machine("x86_64") == "x86_64"


def test_external_env_strips_private_variables_case_insensitively(monkeypatch):
    from mcmanager.bundle import external_env

    monkeypatch.setenv("qt_plugin_path", "C:/bundle/plugins")  # Windows names are case-insensitive
    monkeypatch.setenv("PYTHONHOME", "C:/bundle")
    monkeypatch.setenv("MY_SETTING", "kept")
    env = external_env()
    assert "qt_plugin_path" not in env and "PYTHONHOME" not in env
    assert env["MY_SETTING"] == "kept"


def test_detached_process_options():
    from mcmanager.bundle import detached_process_kwargs

    if sys.platform == "win32":
        flags = detached_process_kwargs()["creationflags"]
        assert flags & subprocess.CREATE_NEW_PROCESS_GROUP and flags & subprocess.DETACHED_PROCESS
        assert detached_process_kwargs(hide_console=True)["creationflags"] & subprocess.CREATE_NO_WINDOW
    else:
        assert detached_process_kwargs() == {"start_new_session": True}


# ------------------------------------------------------------------ Prism Launcher on Windows
def _fake_prism(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    exe = folder / "prismlauncher.exe"
    exe.write_bytes(b"MZ")
    return exe


@pytest.fixture
def win_env(monkeypatch, tmp_path):
    from mcmanager.prism import locator

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(locator, "_registry_installs", lambda: [])
    monkeypatch.setattr(locator.shutil, "which", lambda _name: None)
    for var in ("ProgramFiles", "ProgramFiles(x86)", "SCOOP"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "User Name" / "AppData" / "Local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "User Name" / "AppData" / "Roaming"))
    return tmp_path


def test_finds_prism_installed_for_current_user(win_env):
    from mcmanager.prism.locator import locate_prism

    exe = _fake_prism(win_env / "User Name" / "AppData" / "Local" / "Programs" / "PrismLauncher")
    install = locate_prism()
    assert install.executable == exe and install.can_launch
    assert install.data_dir == win_env / "User Name" / "AppData" / "Roaming" / "PrismLauncher"


def test_finds_prism_from_registry_and_program_files(win_env, monkeypatch):
    from mcmanager.prism import locator

    reg = _fake_prism(win_env / "Program Files" / "Prism Launcher Custom")
    monkeypatch.setattr(locator, "_registry_installs", lambda: [(reg.parent, "11.1.1")])
    install = locator.locate_prism()
    assert install.executable == reg and install.version == "11.1.1"

    monkeypatch.setattr(locator, "_registry_installs", lambda: [])
    monkeypatch.setenv("ProgramFiles", str(win_env / "Program Files"))
    machine = _fake_prism(win_env / "Program Files" / "PrismLauncher")
    assert locator.locate_prism().executable == machine


def test_portable_prism_keeps_data_next_to_exe(win_env):
    from mcmanager.prism.locator import locate_prism

    portable = win_env / "Games" / "Prism Portable"
    exe = _fake_prism(portable)
    (portable / "portable.txt").write_text("")
    (portable / "instances").mkdir()
    install = locate_prism(app_override=str(exe))
    assert install.data_dir == portable and install.instances_dir == portable / "instances"
    assert not install.data_dir_overridden  # Prism finds its portable data by itself, no --dir


def test_prism_override_accepts_folder_quoted_path_and_reports_bad_paths(win_env):
    from mcmanager.prism.locator import locate_prism

    folder = win_env / "My Apps" / "Prism"
    exe = _fake_prism(folder)
    assert locate_prism(app_override=str(folder)).executable == exe
    assert locate_prism(app_override=f'"{exe}"').executable == exe  # pasted from Explorer's "Copy as path"
    missing = locate_prism(app_override=str(win_env / "nope" / "prismlauncher.exe"))
    assert not missing.can_launch and any("not a valid" in w for w in missing.warnings)


def test_missing_prism_is_reported_not_raised(win_env):
    from mcmanager.prism import LaunchError, LaunchRequest, build_launch_command, locate_prism

    install = locate_prism()
    assert install.executable is None and not install.can_launch
    with pytest.raises(LaunchError):
        build_launch_command(install, LaunchRequest("Main"))


def test_launch_command_keeps_paths_with_spaces_intact(tmp_path):
    from mcmanager.prism import LaunchRequest, build_launch_command
    from mcmanager.prism.locator import PrismInstallation

    exe = _fake_prism(tmp_path / "Program Files" / "PrismLauncher")
    install = PrismInstallation(executable=exe, app_bundle=None, data_dir=tmp_path / "Prism Data",
                                instances_dir=tmp_path / "Prism Data" / "instances", data_dir_overridden=True)
    cmd = build_launch_command(install, LaunchRequest("My Survival World", profile="Steve"))
    assert cmd == [str(exe), "--dir", str(tmp_path / "Prism Data"), "--launch", "My Survival World",
                   "--profile", "Steve"]
    line = subprocess.list2cmdline(cmd)  # how Windows receives it: each spaced argument quoted
    assert f'"{exe}"' in line and '"My Survival World"' in line


# ------------------------------------------------------------------ game process matching (Windows rules)
def test_process_matching_with_windows_path_rules(monkeypatch):
    from mcmanager.runtime import process_finder as pf

    monkeypatch.setattr(pf, "_norm", ntpath.normcase)
    monkeypatch.setattr(pf, "_PATHSEP", ";")
    inst = Path("C:/Users/Some One/AppData/Roaming/PrismLauncher/instances/1.21 Survival")
    javaw = r"C:\Program Files\Java\bin\javaw.exe"
    natives = r"-Djava.library.path=c:\users\SOME ONE\appdata\roaming\prismlauncher\instances\1.21 Survival\natives"
    assert pf.process_matches([javaw, natives + r";C:\other"], None, inst)
    forward = "-Djava.library.path=C:/Users/Some One/AppData/Roaming/PrismLauncher/instances/1.21 Survival/natives"
    assert pf.process_matches([javaw, forward], None, inst)  # Prism may write forward slashes
    copy = natives.replace("1.21 Survival", "1.21 Survival copy")
    assert not pf.process_matches([javaw, copy], None, inst)
    assert pf.process_matches([javaw], r"C:\Users\Some One\AppData\Roaming\PrismLauncher\instances\1.21 Survival\minecraft\\", inst)
    assert not pf.process_matches([javaw], r"C:\Users\Some One\Desktop", inst)
    assert not pf.process_matches([javaw, "-d"], None, inst)  # odd arguments don't break matching


def test_real_process_is_found_and_force_quit(tmp_path):
    """Start a stand-in game process, find it through the real scanner, then kill it (any OS)."""
    from mcmanager.bundle import detached_process_kwargs
    from mcmanager.runtime.process_finder import kill_process, scan_game_processes

    inst = tmp_path / "instances" / "Stand In"
    (inst / "minecraft").mkdir(parents=True)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)",
                             f"-Djava.library.path={inst / 'natives'}"],
                            cwd=inst / "minecraft", **detached_process_kwargs(hide_console=True))
    try:
        found = []
        for _ in range(50):
            found = scan_game_processes([inst])[str(inst)]
            if found:
                break
            time.sleep(0.1)
        assert [p.pid for p in found] == [proc.pid]
        assert kill_process(found[0])
        assert proc.wait(timeout=10) is not None
        assert not kill_process(found[0])  # a gone (or recycled) PID is never signalled
    finally:
        if proc.poll() is None:
            proc.kill()


# ------------------------------------------------------------------ capture backend selection and helpers
def test_capture_backend_selection_on_windows(monkeypatch):
    from mcmanager.windows import capture
    from mcmanager.windows.win32 import Win32CaptureBackend

    monkeypatch.setattr(sys, "platform", "win32")
    assert isinstance(capture.create_backend("auto"), Win32CaptureBackend)
    assert capture.create_backend("auto").method == "printwindow"
    assert capture.create_backend("screen").method == "screen"
    assert capture.create_backend("screencapturekit").method == "printwindow"  # a macOS setting


def test_black_frame_detection_and_bgra_conversion():
    from mcmanager.windows.win32 import bgra_to_qimage, is_mostly_black

    w, h = 400, 200
    assert is_mostly_black(bytearray(w * h * 4))
    red = bytearray(b"\x00\x00\xff\x00" * (w * h))  # BGRA with alpha 0, as GDI leaves it
    assert not is_mostly_black(red)
    img = bgra_to_qimage(red, w, h, 100)
    assert (img.width(), img.height()) == (100, 50)
    assert img.pixel(50, 25) == 0xFFFF0000  # opaque red


def test_tray_notifications_only_on_windows():
    from mcmanager.notifications import TrayNotificationBackend

    if sys.platform != "win32":
        assert not TrayNotificationBackend.available()


def test_settings_save_retries_while_file_is_locked(monkeypatch, tmp_path):
    from mcmanager.config import settings as settings_mod

    monkeypatch.setattr(sys, "platform", "win32")
    real_replace, calls = os.replace, []

    def flaky_replace(src, dst):
        calls.append(dst)
        if len(calls) < 3:
            raise PermissionError(5, "Access is denied")  # e.g. antivirus scanning the file
        real_replace(src, dst)

    monkeypatch.setattr(settings_mod.os, "replace", flaky_replace)
    target = tmp_path / "settings.json"
    settings_mod.atomic_write_json(target, {"ok": True})
    assert len(calls) == 3 and settings_mod.read_json_or_backup(target) == {"ok": True}
    assert not list(tmp_path.glob(".settings-*.tmp"))  # no temp files left behind


# ------------------------------------------------------------------ Windows packaging files
def test_windows_icon_is_a_multi_size_ico():
    data = (ROOT / "packaging" / "AppIcon.ico").read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    sizes = {struct.unpack("<B", data[6 + 16 * i:7 + 16 * i])[0] or 256 for i in range(count)}
    assert (reserved, kind) == (0, 1) and {16, 32, 48, 256} <= sizes


def test_windows_spec_and_build_script():
    spec = (ROOT / "packaging" / "mcmanager_windows.spec").read_text(encoding="utf-8")
    assert "console=False" in spec and "console=True" in spec  # windowed app + console diagnostics
    assert "uac_admin=False" in spec and "AppIcon.ico" in spec
    script = (ROOT / "packaging" / "build_windows.ps1").read_text(encoding="utf-8")
    for needle in ("--self-check", "--mock", "0x8664", "qwindows.dll", "MCMANAGER_DATA_DIR", "Windows-x64.zip"):
        assert needle in script
    wf = (ROOT / ".github" / "workflows" / "build-windows.yml").read_text(encoding="utf-8")
    assert "runs-on: windows-" in wf and "build_windows.ps1" in wf
    assert "gh release" not in wf and "softprops" not in wf  # builds artifacts only, never publishes
    mac_wf = (ROOT / ".github" / "workflows" / "build-macos.yml").read_text(encoding="utf-8")
    assert "!v*-windows*" in mac_wf  # Windows preview tags don't trigger macOS builds


def test_macos_only_dependencies_are_marked():
    for line in (ROOT / "requirements.txt").read_text().splitlines():
        if line.lower().startswith("pyobjc"):
            assert 'sys_platform == "darwin"' in line


# ------------------------------------------------------------------ real Win32 API (Windows only)
_WINDOW_SCRIPT = textwrap.dedent("""
    import sys
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QWidget
    app = QApplication(sys.argv)
    w = QWidget()
    w.setWindowTitle("mcm test window")
    w.setStyleSheet("background: #3080ff;")
    w.resize(480, 320)
    w.show()
    QTimer.singleShot(60000, app.quit)
    sys.exit(app.exec())
""")


@windows_only
def test_win32_find_capture_focus_and_close_a_real_window():
    from mcmanager.windows import win32
    from mcmanager.windows.focus import ActionResult

    env = {k: v for k, v in os.environ.items() if k.upper() != "QT_QPA_PLATFORM"}  # a real window
    proc = subprocess.Popen([sys.executable, "-c", _WINDOW_SCRIPT], env=env)
    try:
        info = None
        for _ in range(150):
            info = win32.find_windows({proc.pid}).get(proc.pid)
            if info:
                break
            time.sleep(0.1)
        assert info is not None, "the test window was not found"
        assert info.title == "mcm test window" and info.width >= 300 and info.height >= 200

        image = None
        for _ in range(30):  # the first frames may not be painted yet
            try:
                image = win32.Win32CaptureBackend("printwindow").capture(info, 240)
                break
            except Exception:
                time.sleep(0.2)
        assert image is not None and image.width() == 240
        r, g, b = (image.pixel(120, image.height() // 2) >> s & 0xFF for s in (16, 8, 0))
        assert abs(r - 0x30) < 24 and abs(g - 0x80) < 24 and abs(b - 0xFF) < 24, (r, g, b)

        assert isinstance(win32.bring_to_front(proc.pid), ActionResult)  # focus rules depend on the session
        result = win32.close_window_gracefully(proc.pid)
        assert result.ok, result.message
        assert proc.wait(timeout=15) == 0  # WM_CLOSE closed the window and the app quit normally
    finally:
        if proc.poll() is None:
            proc.kill()


@windows_only
def test_win32_window_service_without_windows():
    from mcmanager.windows.service import Win32WindowService, create_window_service

    service = create_window_service()
    assert isinstance(service, Win32WindowService)
    assert service.find_windows({os.getpid()}) == {}  # offscreen test process: no windows
    assert not service.close_gracefully(os.getpid()).ok
    assert not service.bring_to_front(4_000_000).ok  # no such process


@windows_only
def test_registry_lookup_is_read_only_and_safe():
    from mcmanager.prism import locator

    installs = locator._registry_installs()
    assert isinstance(installs, list)
    assert all(isinstance(folder, Path) for folder, _ in installs)
