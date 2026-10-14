# Distributing the app to other Macs

Status of each piece, and what's needed. **Only the first row has actually been done and tested.**

| | Status |
|---|---|
| Apple silicon build, local signing, run on the building Mac | ✅ Built and tested (macOS 15.3, arm64) |
| Intel / universal build | ❌ Not built or tested |
| Developer ID signing | ❌ Not done (needs a paid Apple Developer account) |
| Notarization | ❌ Not done |
| DMG | ❌ Not created or tested |
| Clean-Mac install test | ❌ Not done |

## 1. Architecture: Intel and universal builds

PyInstaller builds for the architecture of the Python running it. On the reference build machine,
Python (python.org universal2 installer), Qt/PySide6 and PyObjC are universal (x86_64 + arm64),
and **psutil is arm64 only**: psutil publishes separate per-architecture wheels for macOS.

Options, simplest first:

1. **Separate Intel build** on an Intel Mac (or an Intel CI runner, if your CI provider still
   offers one), using the same `packaging/build_app.sh`. Publish two downloads.
2. **Universal2 build**: install the arm64 and x86_64 psutil wheels, merge the
   `_psutil_osx*.so` files with `lipo -create`, then build with `target_arch="universal2"` in
   `packaging/mcmanager.spec`. Must be tested on both kinds of Mac (and under Rosetta).

## 2. Signing

- **Today:** builds are signed with a self-signed local certificate (`packaging/setup_signing.sh`)
  or ad-hoc. Either way, other Macs treat the app as coming from an unidentified developer.
  The local certificate keeps macOS permissions stable across rebuilds *on the building Mac*.
- **For public releases:** join the Apple Developer Program (paid), create a **Developer ID
  Application** certificate, then in the spec set `codesign_identity` to it and an
  `entitlements_file`. Developer ID signing requires the **hardened runtime**; a Python/Qt app
  usually needs entitlements such as `com.apple.security.cs.allow-unsigned-executable-memory`
  and `com.apple.security.cs.disable-library-validation`; test which are really required. Re-sign
  every nested binary (PyInstaller does this when an identity is given), then
  `codesign --verify --deep --strict` and `spctl -a -vvv`.
- Keep the bundle identifier stable once released (changing it resets users' permissions). The
  current `local.MinecraftMultiAccountManager` is fine for local builds; a public project would
  normally use a reverse-DNS name it controls, e.g. `io.github.<user>.<repo>`.

## 3. Notarization

After Developer ID signing: zip or DMG the app, submit with
`xcrun notarytool submit <file> --keychain-profile <profile> --wait`, then
`xcrun stapler staple` the app (and the DMG). Notarization requires the hardened runtime and a
secure timestamp (`codesign --timestamp`).

## 4. DMG

```bash
mkdir -p build/dmg && cp -R "dist/Multi-Account Manager for Minecraft.app" build/dmg/
ln -sf /Applications build/dmg/Applications
hdiutil create -volname "Multi-Account Manager for Minecraft" -srcfolder build/dmg -ov -format UDZO \
  "dist/Multi-Account-Manager-for-Minecraft-<version>-arm64.dmg"
```

Sign (and notarize) the DMG too. Include the architecture in the file name until a universal
build exists.

## 5. Gatekeeper without notarization

A downloaded, un-notarized app is quarantined. On first launch macOS says it can't verify the
app; users must go to **System Settings › Privacy & Security › Open Anyway** (macOS 15 removed
the right-click › Open shortcut). Document this on the Releases page. Never tell users to disable
Gatekeeper or run `xattr -cr` on downloads they don't trust.

## 6. Clean-machine test checklist

On a Mac (or a fresh user account) **without** Python, Homebrew or this repository:

- [ ] Download the release, verify its checksum, open it (Gatekeeper flow as documented).
- [ ] App opens; Prism Launcher in /Applications is detected; instances are listed.
- [ ] Screen Recording prompt/flow works; previews appear after reopening.
- [ ] Accessibility (optional) works: Bring to Front restores a minimized game.
- [ ] Notifications: permission prompt appears, test notification is shown.
- [ ] Launch, stop, restart a real game; crash detection; play time survives a restart.
- [ ] Settings and play time are created under `~/Library/Application Support/…`, not in the app.
- [ ] Replacing the app with a newer build keeps settings and (if signing identity is stable) permissions.
- [ ] Repeat on Intel if an Intel/universal build is published.

## 7. Licenses in releases

The build copies `THIRD_PARTY_NOTICES.md` and the license texts (LGPL-3.0, GPL-3.0, Python, psutil,
PyObjC) into `Contents/Resources/licenses/`, which `build_app.sh` verifies. Attach the same
notices to each GitHub release, and keep the project's own `LICENSE` in the DMG.
