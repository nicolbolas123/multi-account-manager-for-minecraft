# Distributing the app to other Macs

(Windows: see [WINDOWS.md](WINDOWS.md) for the Windows build, its packaging script
`packaging/build_windows.ps1` and the `build-windows.yml` workflow. From v0.3.0 a release has
the two macOS zips and the Windows zip; pushing a `v*` tag builds all three. Tags like
`v0.3.0-windows-preview.1` were Windows-only test pre-releases, which the macOS workflow ignores.)

## Status

| | Status |
|---|---|
| Apple silicon (arm64) build | ✅ Built natively and tested with real Minecraft (macOS 15.3) |
| Intel (x86_64) build | ✅ Built under Rosetta 2 on Apple silicon: every binary is x86_64, tests, self-check and mock mode pass as x86_64. ❓ **Not yet run on a real Intel Mac** (see the [checklist](#6-intel-mac-test-checklist)) |
| CI builds on native runners | ⚙️ Workflow written (`.github/workflows/build-macos.yml`); not yet run on GitHub |
| Universal build | ❌ Deliberately not produced (see below) |
| Developer ID signing / notarization | ❌ Not done (needs a paid Apple Developer account) |
| DMG | ❌ Releases are zips |

## 1. Architecture strategy: two separate builds

Each release has **two downloads**, each containing only its own architecture:

| Download | For | Built with |
|---|---|---|
| `…-macOS-AppleSilicon.zip` | Apple silicon Macs (M1 and newer), macOS 14+ | `packaging/build_app.sh --arch arm64` |
| `…-macOS-Intel.zip` | Intel Macs, macOS 14+ | `packaging/build_app.sh --arch x86_64` |

**Why not one universal app?** A universal app needs *every* bundled binary to contain both
architectures. Python (python.org universal2 installer), Qt/PySide6 and PyObjC ship universal2
wheels, but **psutil publishes separate arm64 and x86_64 wheels**. A universal build would mean
downloading both psutil wheels, merging their libraries with `lipo`, and building with
`target_arch="universal2"`: a hand-made combination that no upstream project tests, in a twice-as-large
download (~70 MB zipped instead of ~33 MB), that would still need testing on both kinds of Mac.
Separate builds use every package exactly as published for that architecture, and PyInstaller
refuses any binary that lacks the target architecture. If psutil ever ships universal2 wheels,
a universal build becomes reasonable: build with `target_arch="universal2"` and run
`verify_bundle.py` for *both* architectures.

**Minimum macOS is 14 Sonoma** for both builds (`LSMinimumSystemVersion`), because PySide6 6.12
(Qt 6.12) is published for macOS 14+. Intel Macs that run Sonoma: MacBook Air/Pro and Mac mini
2018+, iMac 2019+, iMac Pro, Mac Pro 2019. Apple has said macOS 26 Tahoe is the last macOS for
Intel Macs, and future Qt or PyObjC releases may drop x86_64: check before upgrading the pins.

## 2. Building

`packaging/build_app.sh --arch <arm64|x86_64>` builds into `dist/<arch>/`. Without `--arch` it
builds this Mac's architecture into `dist/` (the usual place for your own copy).

| Building on | arm64 | x86_64 |
|---|---|---|
| Apple silicon Mac | native | Rosetta 2: the venv, pip, tests, PyInstaller and the app's self-check all run as x86_64 (`arch -x86_64`), so pip installs the x86_64 psutil wheel |
| Intel Mac | refused (can't run arm64 code to test it) | native |

Requirements: Python 3.11+ from python.org's universal2 installer (releases use **3.14**),
Xcode command-line tools, and Rosetta 2 for Intel builds on Apple silicon. Dependencies are pinned
in `packaging/constraints.txt` so both architectures use identical versions.

After building, the script runs `packaging/verify_bundle.py`, which checks with `lipo` and
`otool` that all ~113 Mach-O files (executable, Python, Qt frameworks and plugins, PyObjC and
psutil extension modules) contain the target architecture, that every linked library is in the
bundle or in macOS (nothing from Homebrew, the python.org framework or a virtualenv), and the
Info.plist (bundle identifier, `MCMBuildArch`, `LSMinimumSystemVersion`). Then the app's own
`--self-check` runs as the target architecture and must report it.

**Is a Rosetta-built Intel app good enough?** The packages installed under Rosetta are the
ordinary x86_64 wheels and PyInstaller copies them unchanged, so the result should be the same as
a build on an Intel Mac. What Rosetta can't show is how the app behaves on Intel *hardware*
(GPU, window server, ScreenCaptureKit performance). The workflow below builds on a native Intel
runner; use that build for releases, and test it on a real Intel Mac before calling it supported.

### GitHub Actions

`.github/workflows/build-macos.yml` builds on `macos-15` (Apple silicon) and `macos-15-intel`
(Intel), each natively, when you press **Run workflow** on the Actions tab or push a `v*` tag.
Each job runs the tests and all checks, then uploads the zip and `.sha256` as an artifact
(`macOS-arm64`, `macOS-x86_64`) on the run's summary page. It never creates or edits a release.
CI builds are **ad-hoc signed** (no certificate on the runner); that makes no difference to
users, because neither ad-hoc nor the local certificate is trusted on other Macs. GitHub plans to
retire its Intel runners eventually; after that, Intel builds need an Intel Mac or Rosetta.

## 3. Release zips

```bash
packaging/make_release.sh "dist/arm64/Multi-Account Manager for Minecraft.app"
packaging/make_release.sh "dist/x86_64/Multi-Account Manager for Minecraft.app"
```

Writes `dist/release/Multi-Account-Manager-for-Minecraft-<version>-macOS-AppleSilicon.zip` /
`…-macOS-Intel.zip` and a `.sha256` file for each. The label comes from the app's own
`MCMBuildArch` and is re-verified, so a zip can't be mislabeled. Existing zips are never
overwritten.

## 4. Publishing on GitHub (without touching existing releases)

1. Raise `__version__` in `mcmanager/__init__.py` (and `version` in `pyproject.toml`), e.g. to
   `0.2.0`, commit and push. Don't add new downloads to the v0.1.0 release: v0.1.0's code is
   different, and its Apple silicon zip stays as it is.
2. Get both zips: either run the workflow (Actions › Build macOS apps › Run workflow, then
   download the two artifacts and unzip them, giving the release zips and `.sha256` files), or
   build locally with the commands above.
3. Releases › **Draft a new release**, tag `v0.2.0`, attach both zips (and
   `THIRD_PARTY_NOTICES.md`). In the notes, say which zip is for which Mac, that macOS 14+ is
   required, the Open Anyway step (below), both SHA-256 checksums, and whether the Intel build has
   been tested on a real Intel Mac.
4. Leave older releases as they are. "Set as the latest release" makes the new one the default.

## 5. Signing, notarization and Gatekeeper (separate from architecture)

- **Today:** builds are signed with a self-signed local certificate (`packaging/setup_signing.sh`,
  for stable permissions on the building Mac) or ad-hoc (CI). On other Macs both mean
  "unidentified developer". A downloaded app is quarantined; on first launch macOS says it can't
  verify the app; users go to **System Settings › Privacy & Security › Open Anyway** (macOS 15
  removed the right-click › Open shortcut). Never tell users to disable Gatekeeper or run
  `xattr -cr` on downloads they don't trust.
- **For a smooth install:** join the Apple Developer Program (paid), create a **Developer ID
  Application** certificate, set `codesign_identity` and an `entitlements_file` in the spec, and
  enable the **hardened runtime** (a Python/Qt app usually needs
  `com.apple.security.cs.allow-unsigned-executable-memory` and
  `com.apple.security.cs.disable-library-validation`; test which are really required). Then
  notarize each zip/DMG with `xcrun notarytool submit <file> --keychain-profile <profile> --wait`
  and `xcrun stapler staple` the app. Do this for **both** architectures; in CI, the certificate
  and notary credentials go into encrypted repository secrets.
- Keep the bundle identifier stable once released (changing it resets users' permissions). The
  current `local.MinecraftMultiAccountManager` is fine for now; a project with Developer ID would
  normally use a reverse-DNS name it controls, e.g. `io.github.<user>.<repo>`.

## 6. Intel Mac test checklist

On a real **Intel** Mac with macOS 14 or later (ideally also a fresh user account without Python
or Homebrew), using the Intel zip from a release candidate:

- [ ] `shasum -a 256` of the download matches; unzip; move to Applications; Open Anyway works.
- [ ] Activity Monitor › CPU › **Kind** column shows **Intel** for the app.
- [ ] App opens; the window looks right (fonts, icons, dark mode); `--self-check` (run
      `"/Applications/Multi-Account Manager for Minecraft.app/Contents/MacOS/Multi-Account Manager for Minecraft" --self-check`)
      prints `"ok": true`, `"machine": "x86_64"`, `"rosetta": false`.
- [ ] Mock mode: same binary with `--mock`; Add All Prism Instances; launch all; previews
      animate; "Mock Crashy" is reported as crashed after ~20 s.
- [ ] Prism Launcher in /Applications is detected; instances and account profiles are listed.
- [ ] Launch a real game; status goes Launching → Running; username/server appear.
- [ ] Screen Recording prompt/flow works; live preview appears after reopening the app.
- [ ] Accessibility (optional): Bring to Front restores a minimized game.
- [ ] Notifications: permission prompt appears; the test notification is shown.
- [ ] Stop, Restart, Force Quit; crash detection; play time survives quitting and reopening.
- [ ] Close the dashboard while a game runs, reopen: it re-attaches.
- [ ] Settings and play time are in `~/Library/Application Support/MinecraftMultiAccountManager/`,
      nothing is written inside the app.
- [ ] Replacing the app with a newer build keeps settings and play time.
- [ ] The app log (`~/Library/Logs/MinecraftMultiAccountManager/app.log`) shows
      `CPU architecture: x86_64 (Rosetta: False)` and no errors.

The same checklist, with the Apple silicon zip, applies to clean Apple silicon Macs.

## 7. Licenses in releases

The build copies `THIRD_PARTY_NOTICES.md` and the license texts (LGPL-3.0, GPL-3.0, Python, psutil,
PyObjC) into `Contents/Resources/licenses/`, which `build_app.sh` verifies. Attach the same
notices to each GitHub release.
