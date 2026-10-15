# PyInstaller spec for the macOS app bundle. Build with packaging/build_app.sh (not directly).
# -*- mode: python -*-
import os
import re
from pathlib import Path

ROOT = Path(SPECPATH).parent  # noqa: F821  (SPECPATH is provided by PyInstaller)
init = (ROOT / "mcmanager" / "__init__.py").read_text()
APP_NAME = re.search(r'APP_NAME = "([^"]+)"', init).group(1)
BUNDLE_ID = re.search(r'APP_BUNDLE_ID = "([^"]+)"', init).group(1)
VERSION = re.search(r'__version__ = "([^"]+)"', init).group(1)
# One architecture per build (arm64 or x86_64), set by build_app.sh. PyInstaller then thins
# universal libraries to it and refuses any binary that lacks it (e.g. a wrong psutil wheel).
TARGET_ARCH = os.environ.get("MCM_TARGET_ARCH") or None
# Oldest macOS the bundled Qt (PySide6 6.12, wheel tag macosx_14_0) supports.
MIN_MACOS = "14.0"

# License texts shipped inside the app (Contents/Resources/licenses), see THIRD_PARTY_NOTICES.md.
def _license_datas():
    import sys
    from importlib.metadata import distribution

    out = [(str(ROOT / "LICENSE"), "licenses"),
           (str(ROOT / "THIRD_PARTY_NOTICES.md"), "licenses"),
           (str(ROOT / "packaging" / "licenses" / "LGPL-3.0.txt"), "licenses"),
           (str(ROOT / "packaging" / "licenses" / "GPL-3.0.txt"), "licenses")]
    python_license = Path(sys.base_prefix) / "lib" / f"python{sys.version_info[0]}.{sys.version_info[1]}" / "LICENSE.txt"
    if python_license.is_file():
        out.append((str(python_license), "licenses/Python"))
    for dist_name, folder in (("psutil", "psutil"), ("pyobjc-framework-Cocoa", "PyObjC")):
        for f in distribution(dist_name).files or []:
            if "licen" in f.name.lower():
                out.append((str(f.locate()), f"licenses/{folder}"))
    if len(out) < 6:
        raise SystemExit(f"Expected license files are missing: {out}")
    return out


a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "mcmanager" / "ui" / "assets"), "mcmanager/ui/assets")] + _license_datas(),
    hiddenimports=[
        # imported lazily (inside functions) so PyInstaller's static analysis can miss them
        "mcmanager.mock.environment", "mcmanager.mock.fake_client",
        "ScreenCaptureKit", "Quartz", "AppKit", "Foundation", "ApplicationServices", "UserNotifications",
        "PySide6.QtSvg",
    ],
    excludes=["tkinter", "pytest", "PySide6.QtWebEngineCore", "PySide6.QtQml", "PySide6.QtQuick"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    console=False,  # no Terminal window
    argv_emulation=False,
    target_arch=TARGET_ARCH,  # None = the architecture the build runs under
    codesign_identity=None,  # ad-hoc signature (no Apple Developer account needed)
    entitlements_file=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name=APP_NAME)  # noqa: F821

app = BUNDLE(  # noqa: F821
    coll,
    name=f"{APP_NAME}.app",
    icon=str(ROOT / "packaging" / "AppIcon.icns"),
    bundle_identifier=BUNDLE_ID,
    version=VERSION,
    info_plist={
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": MIN_MACOS,
        "MCMBuildArch": TARGET_ARCH or os.uname().machine,  # checked by verify_bundle.py
        "LSApplicationCategoryType": "public.app-category.utilities",
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,
        "NSHumanReadableCopyright": "Copyright © 2026 nicolbolas123. MIT License.",
        # Fingerprint of the source files this app was built from (build_app.sh --check compares it).
        "MCMSourceFingerprint": os.environ.get("MCM_SOURCE_FINGERPRINT", "unknown"),
        # Shown by macOS when it asks for these permissions:
        "NSScreenCaptureUsageDescription": "Shows live previews of your Minecraft windows on the dashboard.",
    },
)
