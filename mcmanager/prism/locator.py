"""Locating a Prism Launcher installation and its data directory.

Prism's documented command-line interface (verified with ``prismlauncher
--help`` on Prism 11.1.1) is what the rest of the app relies on; this module
only finds the executable and the folders Prism reads.

macOS: the ``.app`` bundle, identified by its bundle identifier (in /Applications, ~/Applications,
via Spotlight, Downloads, Desktop).
Windows: ``prismlauncher.exe``, found through the installer's uninstall registry entry, the
default per-user and per-machine install folders, Scoop, or PATH. A *portable* Prism (a
``portable.txt`` file next to the exe) keeps its data in its own folder instead of
``%APPDATA%\\PrismLauncher``.
"""

from __future__ import annotations

import logging
import os
import plistlib
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from mcmanager.prism.cfgparse import read_cfg

log = logging.getLogger(__name__)

PRISM_BUNDLE_ID = "org.prismlauncher.PrismLauncher"
APP_BUNDLE_NAME = "Prism Launcher.app"
WINDOWS_EXE_NAME = "prismlauncher.exe"
PORTABLE_MARKER = "portable.txt"  # Prism's portable mode: data lives next to the executable


@dataclass
class PrismInstallation:
    executable: Path | None
    app_bundle: Path | None
    data_dir: Path
    instances_dir: Path
    version: str | None = None
    data_dir_overridden: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def can_launch(self) -> bool:
        return self.executable is not None and self.executable.exists()

    @property
    def data_dir_exists(self) -> bool:
        return self.instances_dir.is_dir()


def default_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PrismLauncher"
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        return (Path(appdata) if appdata else Path.home() / "AppData" / "Roaming") / "PrismLauncher"
    return Path.home() / ".local" / "share" / "PrismLauncher"


def _bundle_info(bundle: Path) -> dict | None:
    try:
        with open(bundle / "Contents" / "Info.plist", "rb") as fh:
            return plistlib.load(fh)
    except (OSError, plistlib.InvalidFileException):
        return None


def executable_for_bundle(bundle: Path) -> Path | None:
    """Return the Prism executable inside ``bundle`` if it really is Prism Launcher.

    The bundle identifier is checked so that unrelated apps that happen to be
    called "Prism" (e.g. GraphPad Prism) are never mistaken for the launcher.
    """
    info = _bundle_info(bundle)
    if not info or info.get("CFBundleIdentifier") != PRISM_BUNDLE_ID:
        return None
    exe = bundle / "Contents" / "MacOS" / info.get("CFBundleExecutable", "prismlauncher")
    return exe if exe.exists() else None


def _spotlight_bundles() -> list[Path]:
    if not shutil.which("mdfind"):
        return []
    try:
        out = subprocess.run(
            ["mdfind", f"kMDItemCFBundleIdentifier == '{PRISM_BUNDLE_ID}'"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [Path(line) for line in out.splitlines() if line.endswith(".app")]


def candidate_bundles() -> list[Path]:
    home = Path.home()
    candidates = [
        Path("/Applications") / APP_BUNDLE_NAME,
        home / "Applications" / APP_BUNDLE_NAME,
        *_spotlight_bundles(),
        home / "Downloads" / APP_BUNDLE_NAME,
        home / "Desktop" / APP_BUNDLE_NAME,
    ]
    seen: set[Path] = set()
    unique = []
    for c in candidates:
        if c not in seen and "/AppTranslocation/" not in str(c):
            seen.add(c)
            unique.append(c)
    return unique


# ---------------------------------------------------------------- Windows

def windows_executable_in(folder: Path) -> Path | None:
    """``prismlauncher.exe`` inside ``folder``, if it is there."""
    exe = folder / WINDOWS_EXE_NAME
    try:
        return exe if exe.is_file() else None
    except OSError:  # inaccessible folder
        return None


def _is_prism_exe(path: Path) -> bool:
    return path.name.lower() == WINDOWS_EXE_NAME


def _registry_installs() -> list[tuple[Path, str | None]]:
    """(install folder, version) of Prism from the installer's Uninstall registry entries.

    Read-only, from both the per-user (HKCU) and per-machine (HKLM) hives; no admin rights needed.
    """
    if sys.platform != "win32":
        return []
    import winreg

    found: list[tuple[Path, str | None]] = []
    roots = [(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
             (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
             (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall")]
    for hive, path in roots:
        try:
            with winreg.OpenKey(hive, path) as uninstall:
                count = winreg.QueryInfoKey(uninstall)[0]
                for i in range(count):
                    try:
                        with winreg.OpenKey(uninstall, winreg.EnumKey(uninstall, i)) as app:
                            values = {}
                            for name in ("DisplayName", "InstallLocation", "DisplayIcon", "DisplayVersion"):
                                try:
                                    values[name] = str(winreg.QueryValueEx(app, name)[0])
                                except OSError:
                                    pass
                    except OSError:
                        continue
                    if not values.get("DisplayName", "").lower().startswith("prism launcher"):
                        continue
                    folder = values.get("InstallLocation") or ""
                    if not folder and values.get("DisplayIcon"):
                        folder = str(Path(values["DisplayIcon"].split(",")[0].strip('"')).parent)
                    if folder:
                        found.append((Path(folder.strip('"')), values.get("DisplayVersion")))
        except OSError:
            continue
    return found


def windows_candidate_dirs() -> list[Path]:
    """Folders that usually contain ``prismlauncher.exe`` (installer, winget, Scoop)."""
    env = os.environ
    home = Path.home()
    dirs = [d for d, _ in _registry_installs()]
    local = Path(env["LOCALAPPDATA"]) if env.get("LOCALAPPDATA") else home / "AppData" / "Local"
    dirs.append(local / "Programs" / "PrismLauncher")  # installer, "just for me" (the default)
    for var in ("ProgramFiles", "ProgramFiles(x86)"):
        if env.get(var):
            dirs.append(Path(env[var]) / "PrismLauncher")  # installer, "for all users"
    scoop = Path(env["SCOOP"]) if env.get("SCOOP") else home / "scoop"
    dirs.append(scoop / "apps" / "prismlauncher" / "current")
    unique: list[Path] = []
    for d in dirs:
        if d not in unique:
            unique.append(d)
    return unique


def is_portable(executable: Path) -> bool:
    return (executable.parent / PORTABLE_MARKER).is_file()


# ---------------------------------------------------------------- both

def resolve_override(path_str: str) -> tuple[Path | None, Path | None]:
    """Accept an ``.app`` bundle, a Prism folder (Windows), or a direct path to the executable."""
    path = Path(path_str.strip().strip('"')).expanduser()
    if path.suffix == ".app":
        return executable_for_bundle(path), path
    try:
        if path.is_dir():
            return windows_executable_in(path), None
        if path.is_file():
            return path, None
    except OSError:  # e.g. a network path that can't be reached
        pass
    return None, None


def instances_dir_for(data_dir: Path) -> Path:
    cfg = read_cfg(data_dir / "prismlauncher.cfg")
    value = cfg.get("InstanceDir", "instances") or "instances"
    path = Path(value).expanduser()
    return path if path.is_absolute() else data_dir / path


def locate_prism(app_override: str | None = None, data_override: str | None = None) -> PrismInstallation:
    warnings: list[str] = []
    executable = bundle = None

    if app_override:
        executable, bundle = resolve_override(app_override)
        if executable is None:
            warnings.append(
                f"The configured Prism Launcher path is not a valid Prism Launcher app: {app_override}"
            )
    version = None
    if executable is None and sys.platform == "darwin":
        for candidate in candidate_bundles():
            exe = executable_for_bundle(candidate)
            if exe:
                executable, bundle = exe, candidate
                break
    if executable is None and sys.platform == "win32":
        versions = {str(d): v for d, v in _registry_installs()}
        for folder in windows_candidate_dirs():
            exe = windows_executable_in(folder)
            if exe:
                executable, version = exe, versions.get(str(folder))
                break
    if executable is None:
        found = shutil.which("prismlauncher")
        if found:
            executable = Path(found)
    if executable is not None and sys.platform == "win32" and not _is_prism_exe(executable):
        warnings.append(f"{executable.name} doesn't look like Prism Launcher (expected {WINDOWS_EXE_NAME}).")

    if bundle is not None:
        info = _bundle_info(bundle) or {}
        version = info.get("CFBundleShortVersionString")
        if bundle.parent == Path.home() / "Downloads":
            warnings.append(
                "Prism Launcher is running from your Downloads folder. macOS runs quarantined "
                "apps from a temporary copy (App Translocation); moving it to /Applications is recommended."
            )

    if data_override:
        data_dir = Path(data_override).expanduser()
    elif executable is not None and sys.platform == "win32" and is_portable(executable):
        data_dir = executable.parent  # portable Prism: instances, accounts and config live next to it
    else:
        data_dir = default_data_dir()
    install = PrismInstallation(
        executable=executable,
        app_bundle=bundle,
        data_dir=data_dir,
        instances_dir=instances_dir_for(data_dir),
        version=version,
        data_dir_overridden=bool(data_override),
        warnings=warnings,
    )
    log.info(
        "Prism: executable=%s version=%s data_dir=%s instances=%s",
        executable, version, data_dir, install.instances_dir,
    )
    return install
