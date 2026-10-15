"""Checks for the per-architecture build tooling (packaging/, .github/workflows/)."""

from __future__ import annotations

import plistlib
import re
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packaging"))

import verify_bundle as vb  # noqa: E402


def test_macho_header_detection():
    assert vb.is_macho_header(b"\xcf\xfa\xed\xfe\x07\x00\x00\x01")  # thin 64-bit
    assert vb.is_macho_header(b"\xca\xfe\xba\xbe\x00\x00\x00\x02")  # universal, 2 architectures
    assert not vb.is_macho_header(b"\xca\xfe\xba\xbe\x00\x00\x00\x34")  # Java class file (version 52)
    assert not vb.is_macho_header(b"#!/bin/sh")
    assert not vb.is_macho_header(b"")


def test_otool_dependencies_must_be_bundled_or_system():
    out = """/x/QtCore:
\t@rpath/QtCore.framework/Versions/A/QtCore (compatibility version 6.0.0, current version 6.12.0)
\t/System/Library/Frameworks/AppKit.framework/Versions/C/AppKit (compatibility version 45.0.0, current version 2575.0.0)
\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0, current version 1351.0.0)
\t/opt/homebrew/opt/openssl@3/lib/libssl.3.dylib (compatibility version 3.0.0, current version 3.0.0)
"""
    deps = vb.parse_otool_dependencies(out)
    assert len(deps) == 4
    assert [vb.dependency_allowed(d) for d in deps] == [True, True, True, False]
    assert not vb.dependency_allowed("/Library/Frameworks/Python.framework/Versions/3.14/Python")


def test_minimum_macos_parsing_and_comparison():
    new = "Load command 9\n      cmd LC_BUILD_VERSION\n  cmdsize 32\n platform 1\n    minos 14.0\n      sdk 15.0\n"
    old = "Load command 8\n      cmd LC_VERSION_MIN_MACOSX\n  cmdsize 16\n  version 10.13\n      sdk 10.14\n"
    assert vb.parse_minos(new) == "14.0"
    assert vb.parse_minos(old) == "10.13"
    assert vb.parse_minos("cmd LC_SEGMENT_64") is None
    assert vb.version_tuple("14.4") > vb.version_tuple("14.0")
    assert vb.version_tuple("26.0") > vb.version_tuple("15.7.1")


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("lipo"), reason="needs macOS developer tools")
def test_verify_reports_architecture_and_metadata(tmp_path):
    app = tmp_path / "Test.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    shutil.copy(Path(sys.executable).resolve(), app / "Contents" / "MacOS" / "Test")
    info = {"CFBundleIdentifier": "local.test", "CFBundleExecutable": "Test",
            "LSMinimumSystemVersion": "99.0", "MCMBuildArch": "x86_64"}
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
    import platform

    report = vb.verify(app, platform.machine(), bundle_id="local.other")
    assert report["macho_files"] == 1
    problems = "\n".join(report["problems"])
    assert "bundle identifier" in problems
    if platform.machine() != "x86_64":
        assert "MCMBuildArch" in problems
    assert not report["ok"]


def test_constraints_pin_every_runtime_dependency():
    pins = {}
    for line in (ROOT / "packaging" / "constraints.txt").read_text().splitlines():
        line = line.split("#")[0].strip()
        if line:
            name, _, version = line.partition("==")
            assert version, f"constraint must be an exact pin: {line}"
            pins[name.lower().replace("_", "-")] = version
    for line in (ROOT / "requirements.txt").read_text().splitlines():
        line = line.split("#")[0].strip()
        if line:
            name = re.split(r"[<>=!~ ]", line)[0].lower().replace("_", "-")
            assert name in pins, f"{name} is not pinned in packaging/constraints.txt"
    assert "pyinstaller" in pins
    assert "-c constraints.txt" in (ROOT / "packaging" / "requirements-build.txt").read_text()


def test_spec_builds_the_requested_architecture():
    spec = (ROOT / "packaging" / "mcmanager.spec").read_text()
    assert 'os.environ.get("MCM_TARGET_ARCH")' in spec
    assert "target_arch=TARGET_ARCH" in spec
    assert '"MCMBuildArch"' in spec
    m = re.search(r'MIN_MACOS = "([\d.]+)"', spec)
    assert m and vb.version_tuple(m.group(1)) >= (14, 0)  # PySide6 6.12 needs macOS 14


def test_build_script_targets_and_release_names():
    build = (ROOT / "packaging" / "build_app.sh").read_text()
    assert 'OUT_DIR="$ROOT/dist/$ARCH"' in build
    assert "verify_bundle.py" in build
    release = (ROOT / "packaging" / "make_release.sh").read_text()
    assert 'arm64) LABEL="AppleSilicon"' in release and 'x86_64) LABEL="Intel"' in release


def test_ci_builds_each_architecture_on_a_native_runner():
    wf = (ROOT / ".github" / "workflows" / "build-macos.yml").read_text()
    assert re.search(r"runner: macos-\d+-intel\s+arch: x86_64", wf)
    assert re.search(r"runner: macos-\d+\s+arch: arm64", wf)
    assert "build_app.sh --arch ${{ matrix.arch }}" in wf
    assert "gh release" not in wf and "softprops" not in wf  # builds artifacts only, never publishes


def test_runtime_arch_reports_machine():
    from mcmanager.bundle import runtime_arch

    info = runtime_arch()
    assert info["machine"] in ("arm64", "x86_64")
    assert info["rosetta"] in (True, False, None)
