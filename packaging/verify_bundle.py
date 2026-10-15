"""Check a built .app for one CPU architecture (run by build_app.sh and make_release.sh).

    python3 packaging/verify_bundle.py "dist/x86_64/<App>.app" --arch x86_64

Checks, using the Xcode command-line tools (lipo, otool):

* every Mach-O file in the bundle (executable, Python, Qt, plugins, extension modules)
  contains a slice for the target architecture; a wrong-architecture file would only fail
  at runtime, when that module or Qt plugin is first loaded;
* every linked library is inside the bundle or part of macOS, never a path that only exists
  on the build machine (Homebrew, the python.org framework, a virtualenv);
* Info.plist carries the expected bundle identifier, build architecture and minimum macOS,
  and the main executable doesn't require a newer macOS than Info.plist declares.

This proves the bundle is internally consistent for the architecture. It does not prove the
app works on a real Mac of that architecture: see the test checklist in docs/DISTRIBUTING.md.
Standard library only, so any Python 3 can run it.
"""

from __future__ import annotations

import argparse
import json
import plistlib
import re
import subprocess
import sys
from pathlib import Path

ARCHS = ("arm64", "x86_64")
_THIN_MAGICS = {b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf"}  # 64-bit Mach-O, either byte order
_FAT_MAGICS = {b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"}  # universal (also the Java .class magic!)
_SYSTEM_PREFIXES = ("/usr/lib/", "/System/Library/")
_BUNDLE_PREFIXES = ("@rpath/", "@loader_path/", "@executable_path/")


def is_macho_header(head: bytes) -> bool:
    """True for a Mach-O (thin 64-bit or universal) file header (first 8 bytes)."""
    if head[:4] in _THIN_MAGICS:
        return True
    if head[:4] in _FAT_MAGICS and len(head) >= 8:
        # A Java class file has the same magic followed by its version (>= 45); a universal
        # binary is followed by its (small) number of architectures.
        return 0 < int.from_bytes(head[4:8], "big") < 20
    return False


def parse_otool_dependencies(output: str) -> list[str]:
    """Linked library paths from ``otool -L`` output (first line is the file name)."""
    deps = []
    for line in output.splitlines()[1:]:
        line = line.strip()
        if line:
            deps.append(line.split(" (compatibility version")[0].strip())
    return deps


def dependency_allowed(dep: str) -> bool:
    """Libraries must come from the bundle or from macOS itself."""
    return dep.startswith(_BUNDLE_PREFIXES) or dep.startswith(_SYSTEM_PREFIXES)


def parse_minos(load_commands: str) -> str | None:
    """Minimum macOS from ``otool -l`` output (LC_BUILD_VERSION or the older LC_VERSION_MIN_MACOSX)."""
    m = re.search(r"cmd LC_BUILD_VERSION.*?\n\s*minos (\S+)", load_commands, re.S)
    if m:
        return m.group(1)
    m = re.search(r"cmd LC_VERSION_MIN_MACOSX.*?\n\s*version (\S+)", load_commands, re.S)
    return m.group(1) if m else None


def version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", v)[:3]) or (0,)


def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def find_macho_files(app: Path) -> list[Path]:
    out = []
    for path in sorted(app.rglob("*")):
        if path.is_file() and not path.is_symlink():
            with open(path, "rb") as fh:
                if is_macho_header(fh.read(8)):
                    out.append(path)
    return out


def verify(app: Path, arch: str, bundle_id: str | None = None) -> dict:
    report: dict = {"app": str(app), "arch": arch, "problems": [], "notes": []}
    problems, notes = report["problems"], report["notes"]
    info_path = app / "Contents" / "Info.plist"
    if not info_path.is_file():
        problems.append("Contents/Info.plist is missing")
        return report
    info = plistlib.loads(info_path.read_bytes())
    if bundle_id and info.get("CFBundleIdentifier") != bundle_id:
        problems.append(f"bundle identifier is {info.get('CFBundleIdentifier')!r}, expected {bundle_id!r}")
    if info.get("MCMBuildArch") != arch:
        problems.append(f"Info.plist MCMBuildArch is {info.get('MCMBuildArch')!r}, expected {arch!r}")
    min_macos = str(info.get("LSMinimumSystemVersion", ""))
    if not min_macos:
        problems.append("Info.plist has no LSMinimumSystemVersion")
    main_exe = app / "Contents" / "MacOS" / str(info.get("CFBundleExecutable", ""))
    if not main_exe.is_file():
        problems.append(f"main executable missing: {main_exe.name}")

    files = find_macho_files(app)
    report["macho_files"] = len(files)
    if not files:
        problems.append("no executables or libraries found in the bundle")
    slices: dict[str, int] = {}
    newest: tuple[tuple[int, ...], str, str] = ((0,), "", "")
    for f in files:
        rel = str(f.relative_to(app))
        archs = _run(["lipo", "-archs", str(f)]).split()
        slices[" ".join(archs)] = slices.get(" ".join(archs), 0) + 1
        if arch not in archs:
            problems.append(f"{rel}: has {'/'.join(archs)}, no {arch} slice")
            continue
        for dep in parse_otool_dependencies(_run(["otool", "-arch", arch, "-L", str(f)])):
            if not dependency_allowed(dep):
                problems.append(f"{rel}: links to {dep}, which is outside the app and macOS")
        minos = parse_minos(_run(["otool", "-arch", arch, "-l", str(f)]))
        if minos and version_tuple(minos) > newest[0]:
            newest = (version_tuple(minos), minos, rel)
        if f == main_exe and minos and min_macos and version_tuple(minos) > version_tuple(min_macos):
            problems.append(f"main executable requires macOS {minos}, Info.plist says {min_macos}")
    report["slices"] = slices
    report["min_macos"] = min_macos
    if newest[1] and min_macos and newest[0] > version_tuple(min_macos):
        # dyld doesn't enforce this for libraries, and PySide6's binding modules declare a newer
        # minimum than they need; worth a look when dependencies change, not a failure.
        notes.append(f"highest library minimum is macOS {newest[1]} ({newest[2]}); "
                     f"Info.plist declares {min_macos}")
    report["ok"] = not problems
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("app", type=Path)
    ap.add_argument("--arch", required=True, choices=ARCHS)
    ap.add_argument("--bundle-id")
    ap.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = ap.parse_args(argv)
    report = verify(args.app, args.arch, args.bundle_id)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        layout = ", ".join(f"{n} {a}" for a, n in sorted(report.get("slices", {}).items()))
        print(f"Binaries:   {report.get('macho_files', 0)} Mach-O files ({layout or 'none'})")
        print(f"Min macOS:  {report.get('min_macos') or '?'} (Info.plist)")
        for note in report["notes"]:
            print(f"Note:       {note}")
        for problem in report["problems"][:40]:
            print(f"PROBLEM:    {problem}", file=sys.stderr)
        if len(report["problems"]) > 40:
            print(f"PROBLEM:    … and {len(report['problems']) - 40} more", file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
