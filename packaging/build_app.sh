#!/bin/zsh
# Build the macOS app ("<APP_NAME>.app", name from mcmanager/__init__.py) for one CPU architecture.
#
#   packaging/build_app.sh                 # this Mac's architecture -> dist/<APP_NAME>.app
#   packaging/build_app.sh --arch arm64    # Apple silicon           -> dist/arm64/<APP_NAME>.app
#   packaging/build_app.sh --arch x86_64   # Intel                   -> dist/x86_64/<APP_NAME>.app
#   packaging/build_app.sh --skip-tests    # skip the test suite
#   packaging/build_app.sh --check [--arch …]  # only report whether that app is up to date with the source
#
# Each architecture is built separately, natively (or under Rosetta 2 for x86_64 on Apple
# silicon), from the same pinned dependencies (packaging/constraints.txt); see
# docs/DISTRIBUTING.md for why there is no universal build. Each architecture has its own virtual
# environment (build/venv-<arch>) and work folder, so source files and the development .venv are
# untouched. User data in ~/Library/Application Support is never read or written by the build.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
APP_NAME="$(sed -n 's/^APP_NAME = "\(.*\)"$/\1/p' mcmanager/__init__.py)"  # single source of truth
BUNDLE_ID="$(sed -n 's/^APP_BUNDLE_ID = "\([^"]*\)".*/\1/p' mcmanager/__init__.py)"
[[ -n "$APP_NAME" && -n "$BUNDLE_ID" ]] || { echo "Could not read APP_NAME/APP_BUNDLE_ID from mcmanager/__init__.py" >&2; exit 1; }
SKIP_TESTS=0
CHECK_ONLY=0
ARCH=""
while (( $# )); do
  case "$1" in
    --skip-tests) SKIP_TESTS=1 ;;
    --check) CHECK_ONLY=1 ;;
    --arch) shift; ARCH="${1:-}" ;;
    --arch=*) ARCH="${1#--arch=}" ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

step() { print -P "\n%B==> $1%b"; }
fail() { print -P "\n%F{red}%BBuild failed:%b%f $1" >&2; exit 1; }

[[ "$(uname)" == "Darwin" ]] || fail "this build produces a macOS app and must run on macOS."

# The Mac's real CPU (hw.optional.arm64 is 1 on Apple silicon, even inside a Rosetta shell).
if [[ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" == "1" ]]; then HOST_ARCH=arm64; else HOST_ARCH=x86_64; fi
if [[ -z "$ARCH" ]]; then
  ARCH="$HOST_ARCH"; OUT_DIR="$ROOT/dist"  # default: this Mac's architecture, at the usual place
else
  [[ "$ARCH" == arm64 || "$ARCH" == x86_64 ]] || fail "--arch must be arm64 (Apple silicon) or x86_64 (Intel), not '$ARCH'."
  OUT_DIR="$ROOT/dist/$ARCH"
fi
APP="$OUT_DIR/$APP_NAME.app"
VENV="$ROOT/build/venv-$ARCH"
RUN=(arch "-$ARCH")  # run every build tool as $ARCH, even when the Python is universal2

# Fingerprint of everything that goes into the app (code, assets, icon, build config, dependency list).
source_fingerprint() {
  find mcmanager packaging/entry.py packaging/mcmanager.spec packaging/AppIcon.icns packaging/licenses \
       packaging/constraints.txt LICENSE THIRD_PARTY_NOTICES.md requirements.txt \
       -type f ! -name '*.pyc' ! -path '*/__pycache__/*' ! -name '.DS_Store' -print0 \
    | sort -z | xargs -0 shasum -a 256 | shasum -a 256 | cut -c1-16
}
built_fingerprint() {
  /usr/libexec/PlistBuddy -c "Print :MCMSourceFingerprint" "$APP/Contents/Info.plist" 2>/dev/null || echo "none"
}

if (( CHECK_ONLY )); then
  [[ -d "$APP" ]] || { print "No app at $APP yet. Run packaging/build_app.sh${ARCH:+ --arch $ARCH}"; exit 1; }
  CURRENT="$(source_fingerprint)"; BUILT="$(built_fingerprint)"
  if [[ "$CURRENT" == "$BUILT" ]]; then
    print "Up to date: $APP was built from the current source ($CURRENT)."
  else
    print "Out of date: $APP was built from $BUILT, the source is now $CURRENT. Rebuild it."
    exit 1
  fi
  exit 0
fi

# --- Can this Mac build $ARCH? ---------------------------------------------------------------
if [[ "$ARCH" == arm64 && "$HOST_ARCH" == x86_64 ]]; then
  fail "arm64 (Apple silicon) builds need an Apple silicon Mac: an Intel Mac can't run arm64 code,
so the tests and the built app's self-check couldn't run. Use an Apple silicon Mac or the
GitHub Actions workflow (.github/workflows/build-macos.yml)."
fi
HOW="native"
if [[ "$ARCH" == x86_64 && "$HOST_ARCH" == arm64 ]]; then
  HOW="Rosetta 2"
  arch -x86_64 /usr/bin/true 2>/dev/null \
    || fail "building for Intel on Apple silicon needs Rosetta 2. Install it with:
    softwareupdate --install-rosetta --agree-to-license"
fi

PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null || fail "python3 not found. Install Python 3.11+ from python.org."
"$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 11))' || fail "Python 3.11+ is required ($("$PYTHON" --version))."
"${RUN[@]}" "$PYTHON" -c 'import platform, sys; sys.exit(platform.machine() != sys.argv[1])' "$ARCH" 2>/dev/null \
  || fail "$PYTHON can't run as $ARCH. Use the python.org \"universal2\" installer (python.org/downloads/macos),
or set PYTHON=/path/to/python3. Homebrew's Python on Apple silicon is arm64-only."

step "Preparing $ARCH build environment (build/venv-$ARCH, $HOW)"
if [[ -x "$VENV/bin/python" ]] && ! "${RUN[@]}" "$VENV/bin/python" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
  fail "$VENV doesn't work as $ARCH (made with another Python?). Delete that folder and build again."
fi
if [[ ! -x "$VENV/bin/python" ]]; then
  "${RUN[@]}" "$PYTHON" -m venv "$VENV" || fail "could not create $VENV"
fi
"${RUN[@]}" "$VENV/bin/python" -m pip install -q --upgrade pip || fail "could not update pip (offline?)"
"${RUN[@]}" "$VENV/bin/python" -m pip install -q -r packaging/requirements-build.txt \
  || fail "could not install the pinned build dependencies for $ARCH (see the pip output above).
If a package has no $ARCH wheel for this Python version, use the Python version in docs/DISTRIBUTING.md."
print "PyInstaller $("${RUN[@]}" "$VENV/bin/python" -m PyInstaller --version), $("${RUN[@]}" "$VENV/bin/python" --version), $ARCH ($HOW)"

if (( ! SKIP_TESTS )); then
  step "Running tests ($ARCH)"
  "${RUN[@]}" "$VENV/bin/python" -m pytest -q -p no:cacheprovider || fail "tests failed; fix them or rerun with --skip-tests."
fi

if [[ ! -f packaging/AppIcon.icns ]]; then
  step "Generating app icon"
  "${RUN[@]}" "$VENV/bin/python" packaging/make_icon.py || fail "could not generate the icon"
fi

if pgrep -f "$APP/Contents/MacOS/" >/dev/null 2>&1; then
  fail "the app is running from $APP. Quit it first, then build again."
fi

step "Building $ARCH app bundle"
export MCM_SOURCE_FINGERPRINT="$(source_fingerprint)"
export MCM_TARGET_ARCH="$ARCH"
"${RUN[@]}" "$VENV/bin/python" -m PyInstaller --noconfirm --clean --log-level WARN \
  --distpath "$OUT_DIR" --workpath "$ROOT/build/pyinstaller-$ARCH" packaging/mcmanager.spec \
  || fail "PyInstaller failed (see the messages above)."
rm -rf "$OUT_DIR/$APP_NAME"  # the intermediate folder; the .app is the product

# Sign with the local certificate (packaging/setup_signing.sh) so every build has the same identity
# and macOS keeps its Screen Recording / Accessibility / Notifications permissions across rebuilds.
SIGN_ID="Minecraft Manager Local Signing"
if security find-identity -p codesigning 2>/dev/null | grep -q "\"$SIGN_ID\""; then
  step "Signing with \"$SIGN_ID\""
  codesign --force --deep --sign "$SIGN_ID" "$APP" \
    || fail "code signing failed. If macOS asked to allow keychain access, choose Always Allow and build again."
else
  print -P "%F{yellow}Note:%f signing ad-hoc. macOS will treat every rebuild as a new app and permissions must be"
  print "      granted again after each build. Run packaging/setup_signing.sh once to fix that."
fi

step "Verifying"
[[ -d "$APP" ]] || fail "$APP was not created."
BIN="$APP/Contents/MacOS/$APP_NAME"
[[ -x "$BIN" ]] || fail "the app's executable is missing."
plist() { /usr/libexec/PlistBuddy -c "Print :$1" "$APP/Contents/Info.plist"; }
print "Bundle ID:  $(plist CFBundleIdentifier)"
print "Name:       $(plist CFBundleDisplayName)  (version $(plist CFBundleShortVersionString))"
print "Arch:       $(plist MCMBuildArch), built $HOW on $HOST_ARCH"
"${RUN[@]}" "$VENV/bin/python" packaging/verify_bundle.py "$APP" --arch "$ARCH" --bundle-id "$BUNDLE_ID" \
  || fail "the app contains files that won't work on $ARCH Macs (list above)."
codesign --verify --deep --strict "$APP" 2>&1 || fail "the app's (ad-hoc) code signature is invalid."
if codesign -d -r- "$APP" 2>&1 | grep -q "certificate leaf"; then
  print "Signature:  $SIGN_ID (stable across rebuilds)"
else
  print "Signature:  ad-hoc (changes with every build)"
fi
SELF_CHECK="$ROOT/build/self-check-$ARCH.json"
"${RUN[@]}" "$BIN" --self-check > "$SELF_CHECK" 2>/dev/null \
  || { cat "$SELF_CHECK" >&2; fail "the built app is missing dependencies or resources (report above)."; }
"${RUN[@]}" "$VENV/bin/python" -c 'import json, sys; sys.exit(json.load(open(sys.argv[1]))["machine"] != sys.argv[2])' \
  "$SELF_CHECK" "$ARCH" || fail "the built app's self-check didn't run as $ARCH ($SELF_CHECK)."
print "Self-check: all dependencies and resources load (ran as $ARCH, $HOW)"
for f in LICENSE THIRD_PARTY_NOTICES.md LGPL-3.0.txt GPL-3.0.txt Python/LICENSE.txt; do
  [[ -f "$APP/Contents/Resources/licenses/$f" ]] || fail "license file missing from the app: licenses/$f"
done
print "Licenses:   third-party notices and license texts included"
[[ "$(built_fingerprint)" == "$(source_fingerprint)" ]] \
  || fail "the source changed during the build; build again."
print "Source:     fingerprint $(built_fingerprint) (matches the current source)"

step "Done"
print "App:  $APP"
print "Size: $(du -sh "$APP" | cut -f1)"
print "Open it from Finder, or: open \"$APP\""
print "Release zip: packaging/make_release.sh \"$APP\""
