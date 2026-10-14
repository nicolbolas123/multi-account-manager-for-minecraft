"""Locating a Prism Launcher installation and its data directory.

Prism's documented command-line interface (verified with ``prismlauncher
--help`` on Prism 11.1.1) is what the rest of the app relies on; this module
only finds the executable and the folders Prism reads.
"""

from __future__ import annotations

import logging
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
    if sys.platform.startswith("win"):
        import os

        return Path(os.environ.get("APPDATA", Path.home())) / "PrismLauncher"
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


def resolve_override(path_str: str) -> tuple[Path | None, Path | None]:
    """Accept either an ``.app`` bundle or a direct path to the executable."""
    path = Path(path_str).expanduser()
    if path.suffix == ".app":
        return executable_for_bundle(path), path
    if path.is_file():
        return path, None
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
    if executable is None:
        for candidate in candidate_bundles():
            exe = executable_for_bundle(candidate)
            if exe:
                executable, bundle = exe, candidate
                break
    if executable is None:
        found = shutil.which("prismlauncher")
        if found:
            executable = Path(found)

    version = None
    if bundle is not None:
        info = _bundle_info(bundle) or {}
        version = info.get("CFBundleShortVersionString")
        if bundle.parent == Path.home() / "Downloads":
            warnings.append(
                "Prism Launcher is running from your Downloads folder. macOS runs quarantined "
                "apps from a temporary copy (App Translocation); moving it to /Applications is recommended."
            )

    data_dir = Path(data_override).expanduser() if data_override else default_data_dir()
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
