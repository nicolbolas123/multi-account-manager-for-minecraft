#!/bin/zsh
# Build the macOS app ("<APP_NAME>.app", name from mcmanager/__init__.py) into dist/.
#
#   packaging/build_app.sh               # test, build, verify
#   packaging/build_app.sh --skip-tests  # skip the test suite
#   packaging/build_app.sh --check       # only report whether dist/ is up to date with the source
#
# Uses its own virtual environment (build/venv) and work folder (build/pyinstaller), so source
# files and the development .venv are untouched. User data in ~/Library/Application Support is
# never read or written by the build.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
APP_NAME="$(sed -n 's/^APP_NAME = "\(.*\)"$/\1/p' mcmanager/__init__.py)"  # single source of truth
[[ -n "$APP_NAME" ]] || { echo "Could not read APP_NAME from mcmanager/__init__.py" >&2; exit 1; }
APP="$ROOT/dist/$APP_NAME.app"
VENV="$ROOT/build/venv"
SKIP_TESTS=0
CHECK_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --skip-tests) SKIP_TESTS=1 ;;
    --check) CHECK_ONLY=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

step() { print -P "\n%B==> $1%b"; }
fail() { print -P "\n%F{red}%BBuild failed:%b%f $1" >&2; exit 1; }

# Fingerprint of everything that goes into the app (code, assets, icon, build config, dependency list).
source_fingerprint() {
  find mcmanager packaging/entry.py packaging/mcmanager.spec packaging/AppIcon.icns packaging/licenses \
       LICENSE THIRD_PARTY_NOTICES.md requirements.txt \
       -type f ! -name '*.pyc' ! -path '*/__pycache__/*' ! -name '.DS_Store' -print0 \
    | sort -z | xargs -0 shasum -a 256 | shasum -a 256 | cut -c1-16
}
built_fingerprint() {
  /usr/libexec/PlistBuddy -c "Print :MCMSourceFingerprint" "$APP/Contents/Info.plist" 2>/dev/null || echo "none"
}

if (( CHECK_ONLY )); then
  [[ -d "$APP" ]] || { print "No app in dist/ yet. Run packaging/build_app.sh"; exit 1; }
  CURRENT="$(source_fingerprint)"; BUILT="$(built_fingerprint)"
  if [[ "$CURRENT" == "$BUILT" ]]; then
    print "Up to date: $APP was built from the current source ($CURRENT)."
  else
    print "Out of date: the app was built from $BUILT, the source is now $CURRENT. Run packaging/build_app.sh"
    exit 1
  fi
  exit 0
fi

[[ "$(uname)" == "Darwin" ]] || fail "this build produces a macOS app and must run on macOS."

PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null || fail "python3 not found. Install Python 3.11+ from python.org."
"$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 11))' || fail "Python 3.11+ is required ($("$PYTHON" --version))."

step "Preparing build environment (build/venv)"
if [[ ! -x "$VENV/bin/python" ]]; then
  "$PYTHON" -m venv "$VENV" || fail "could not create $VENV"
fi
"$VENV/bin/python" -m pip install -q --upgrade pip || fail "could not update pip (offline?)"
"$VENV/bin/python" -m pip install -q -r packaging/requirements-build.txt \
  || fail "could not install build dependencies (see the pip output above)."
print "PyInstaller $("$VENV/bin/python" -m PyInstaller --version), $("$VENV/bin/python" --version)"

if (( ! SKIP_TESTS )); then
  step "Running tests"
  "$VENV/bin/python" -m pip install -q pytest || fail "could not install pytest"
  "$VENV/bin/python" -m pytest -q || fail "tests failed; fix them or rerun with --skip-tests."
fi

if [[ ! -f packaging/AppIcon.icns ]]; then
  step "Generating app icon"
  "$VENV/bin/python" packaging/make_icon.py || fail "could not generate the icon"
fi

if pgrep -f "$APP/Contents/MacOS/" >/dev/null 2>&1; then
  fail "the app is running from dist/. Quit it first, then build again."
fi

step "Building app bundle"
export MCM_SOURCE_FINGERPRINT="$(source_fingerprint)"
"$VENV/bin/python" -m PyInstaller --noconfirm --clean --log-level WARN \
  --distpath "$ROOT/dist" --workpath "$ROOT/build/pyinstaller" packaging/mcmanager.spec \
  || fail "PyInstaller failed (see the messages above)."
rm -rf "$ROOT/dist/$APP_NAME"  # the intermediate folder; the .app is the product

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
codesign --verify --deep --strict "$APP" 2>&1 || fail "the app's (ad-hoc) code signature is invalid."
if codesign -d -r- "$APP" 2>&1 | grep -q "certificate leaf"; then
  print "Signature:  $SIGN_ID (stable across rebuilds)"
else
  print "Signature:  ad-hoc (changes with every build)"
fi
"$BIN" --self-check > "$ROOT/build/self-check.json" 2>/dev/null \
  || { cat "$ROOT/build/self-check.json" >&2; fail "the built app is missing dependencies or resources (report above)."; }
print "Self-check: all dependencies and resources load"
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
