# PyInstaller spec for the Windows build. Build with packaging/build_windows.ps1 (not directly).
# -*- mode: python -*-
#
# Produces dist/windows/<APP_NAME>/ (a folder, zipped for release) containing:
#   <APP_NAME>.exe            windowed app (no console) - what users start
#   <APP_NAME> (console).exe  the same app with a console window, for diagnostics:
#                             shows log output and errors; "--self-check" prints a report
#   _internal/                Python, Qt (PySide6) and the other dependencies
# A folder build (not one-file) starts faster, needs no unpacking to a temp folder on every start,
# and is less likely to be flagged by antivirus heuristics.
import re
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent  # noqa: F821  (SPECPATH is provided by PyInstaller)
init = (ROOT / "mcmanager" / "__init__.py").read_text(encoding="utf-8")
APP_NAME = re.search(r'APP_NAME = "([^"]+)"', init).group(1)
VERSION = re.search(r'__version__ = "([^"]+)"', init).group(1)
NUMERIC_VERSION = tuple(int(n) for n in re.findall(r"\d+", VERSION.split("-")[0])[:3]) + (0,) * 4


def _license_datas():
    """License texts shipped in the program folder (licenses/), see THIRD_PARTY_NOTICES.md."""
    from importlib.metadata import distribution

    out = [(str(ROOT / "LICENSE"), "licenses"),
           (str(ROOT / "THIRD_PARTY_NOTICES.md"), "licenses"),
           (str(ROOT / "packaging" / "licenses" / "LGPL-3.0.txt"), "licenses"),
           (str(ROOT / "packaging" / "licenses" / "GPL-3.0.txt"), "licenses")]
    for candidate in (Path(sys.base_prefix) / "LICENSE.txt", Path(sys.base_prefix) / "LICENSE"):
        if candidate.is_file():
            out.append((str(candidate), "licenses/Python"))
            break
    for f in distribution("psutil").files or []:
        if "licen" in f.name.lower():
            out.append((str(f.locate()), "licenses/psutil"))
    if len(out) < 6:
        raise SystemExit(f"Expected license files are missing: {out}")
    return out


def _version_info():
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
    )

    v = NUMERIC_VERSION[:4]
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=v, prodvers=v),
        kids=[
            StringFileInfo([StringTable("040904B0", [
                StringStruct("CompanyName", "nicolbolas123"),
                StringStruct("FileDescription", APP_NAME),
                StringStruct("FileVersion", VERSION),
                StringStruct("InternalName", "mcmanager"),
                StringStruct("LegalCopyright", "Copyright (c) 2026 nicolbolas123. MIT License."),
                StringStruct("OriginalFilename", f"{APP_NAME}.exe"),
                StringStruct("ProductName", APP_NAME),
                StringStruct("ProductVersion", VERSION),
            ])]),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ],
    )


a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "mcmanager" / "ui" / "assets"), "mcmanager/ui/assets")] + _license_datas(),
    hiddenimports=[
        # imported lazily (inside functions) so PyInstaller's static analysis can miss them
        "mcmanager.mock.environment", "mcmanager.mock.fake_client",
        "mcmanager.windows.win32", "PySide6.QtSvg",
    ],
    excludes=["tkinter", "pytest", "PySide6.QtWebEngineCore", "PySide6.QtQml", "PySide6.QtQuick"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

common = dict(
    exclude_binaries=True,
    icon=str(ROOT / "packaging" / "AppIcon.ico"),
    version=_version_info(),
    upx=False,  # compressed executables are a common antivirus false-positive trigger
    uac_admin=False,  # never ask for administrator rights
)
app_exe = EXE(pyz, a.scripts, [], name=APP_NAME, console=False, **common)  # noqa: F821
console_exe = EXE(pyz, a.scripts, [], name=f"{APP_NAME} (console)", console=True, **common)  # noqa: F821
coll = COLLECT(app_exe, console_exe, a.binaries, a.datas, name=APP_NAME, upx=False)  # noqa: F821
