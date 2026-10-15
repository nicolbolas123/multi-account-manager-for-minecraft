#!/bin/zsh
# Package a built app as a release download, named after the Macs it runs on:
#
#   packaging/make_release.sh "dist/arm64/<APP_NAME>.app"    # -> dist/release/<Name>-<version>-macOS-AppleSilicon.zip
#   packaging/make_release.sh "dist/x86_64/<APP_NAME>.app"   # -> dist/release/<Name>-<version>-macOS-Intel.zip
#
# The architecture is read from the app (Info.plist MCMBuildArch) and re-verified, so a zip can't
# be mislabeled. Writes a .sha256 file next to each zip. Never overwrites an existing zip and
# never uploads anything: publishing on GitHub is a manual step (docs/DISTRIBUTING.md).
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
fail() { print -P "%F{red}%BRelease packaging failed:%b%f $1" >&2; exit 1; }

(( $# == 1 )) || { echo "Usage: packaging/make_release.sh <path to the built .app>" >&2; exit 2; }
APP="${1:A}"
[[ -d "$APP/Contents" ]] || fail "$1 is not an app bundle."
plist() { /usr/libexec/PlistBuddy -c "Print :$1" "$APP/Contents/Info.plist" 2>/dev/null; }
ARCH="$(plist MCMBuildArch)" || fail "the app has no MCMBuildArch; rebuild it with packaging/build_app.sh."
VERSION="$(plist CFBundleShortVersionString)"
BUNDLE_ID="$(sed -n 's/^APP_BUNDLE_ID = "\([^"]*\)".*/\1/p' mcmanager/__init__.py)"
case "$ARCH" in
  arm64) LABEL="AppleSilicon" ;;
  x86_64) LABEL="Intel" ;;
  *) fail "unknown architecture '$ARCH' in the app." ;;
esac
FILE_NAME="$(plist CFBundleName | tr ' ' '-')-$VERSION-macOS-$LABEL"
OUT="$ROOT/dist/release"
ZIP="$OUT/$FILE_NAME.zip"
[[ ! -e "$ZIP" ]] || fail "$ZIP already exists. Move it away first (it is never overwritten)."

python3 packaging/verify_bundle.py "$APP" --arch "$ARCH" --bundle-id "$BUNDLE_ID" \
  || fail "the app failed the $ARCH bundle check (see above)."
codesign --verify --deep --strict "$APP" || fail "the app's code signature is invalid."
CHECK_ARGS=()
[[ "${APP:h}" == "$ROOT/dist/$ARCH" ]] && CHECK_ARGS=(--arch "$ARCH")
if [[ "${APP:h}" == "$ROOT/dist" || "${APP:h}" == "$ROOT/dist/$ARCH" ]]; then
  packaging/build_app.sh --check "${CHECK_ARGS[@]}" >/dev/null \
    || print -P "%F{yellow}Note:%f this app was not built from the current source (packaging/build_app.sh --check)."
fi

mkdir -p "$OUT"
ditto -c -k --keepParent "$APP" "$ZIP"  # ditto keeps the bundle's symlinks, permissions and signature
( cd "$OUT" && shasum -a 256 "${ZIP:t}" > "${ZIP:t}.sha256" )
print "Created:  $ZIP ($(du -h "$ZIP" | cut -f1))"
print "SHA-256:  $(cut -d' ' -f1 "$ZIP.sha256")  (also in ${ZIP:t}.sha256)"
print "For:      $( [[ $ARCH == arm64 ]] && print 'Apple silicon Macs (M1 and newer)' || print 'Intel Macs' ), macOS $(plist LSMinimumSystemVersion) or later"
