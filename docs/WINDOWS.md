# Windows (experimental preview)

> **Status: experimental.** The Windows version is built and tested automatically on a GitHub
> Windows runner (unit tests, the real Win32 window API, and the packaged app in mock mode). The
> first preview was tried on one real Windows PC with Prism Launcher and Minecraft: it mostly
> worked, and the problems found there are fixed in `0.3.0-windows-preview.2`, which still needs
> retesting on a real PC. It isn't fully tested yet: expect bugs, and please report what works and
> what doesn't (see [Reporting problems](#reporting-problems)).

## Requirements

- Windows 10 or 11, **64-bit (x64)**. Windows on ARM isn't built yet (the x64 app may run under
  Windows' emulation; untested).
- [Prism Launcher](https://prismlauncher.org) installed, with an instance and a signed-in account.
- No Python needed, no administrator rights needed.

## Install and start

1. Download `Multi-Account-Manager-for-Minecraft-<version>-Windows-x64.zip` from the
   [Releases page](https://github.com/nicolbolas123/multi-account-manager-for-minecraft/releases)
   (Windows previews are marked *Pre-release*).
2. Optional: check the download. In PowerShell:
   `Get-FileHash $HOME\Downloads\<file>.zip -Algorithm SHA256` and compare with the release notes.
3. Right-click the zip › **Extract All…**, e.g. to `C:\Users\<you>\Apps\`. Don't run it from inside
   the zip.
4. Open the extracted folder and double-click **Multi-Account Manager for Minecraft.exe**.
5. The app isn't code-signed, so **Microsoft Defender SmartScreen** may say *"Windows protected
   your PC"*. Click **More info › Run anyway**. (Only do this for a download from this repository.)

To update, quit the app and replace the folder with the new one. Settings and play time are
stored separately (below) and are kept.

## Where your data is

| What | Where |
|---|---|
| Settings, play time | `%APPDATA%\MinecraftMultiAccountManager\` (`settings.json`, `playtime.json`) |
| Logs | `%LOCALAPPDATA%\MinecraftMultiAccountManager\Logs\app.log` (and `prism-cli.log`) |
| Mock-mode demo data | `%LOCALAPPDATA%\MinecraftMultiAccountManager\Cache\mock\` |

Paste a path into File Explorer's address bar to open it. Nothing is stored in the program folder.

## Feature status on Windows

| Feature | Status | Notes |
|---|---|---|
| Dashboard, tiles, expanded view, search, themes | ✅ Implemented | Shared Qt code; packaged app runs on the Windows runner |
| Groups, reordering (drag & drop), pinning, favorites | ✅ Implemented | Shared code, tested on Windows. preview.2: moving tiles no longer flashes a blank window |
| New Prism instances / accounts appear without restarting | ✅ Implemented (preview.2) | Prism's files are checked every 3 s and when *Add Account* opens; an open Add/Edit dialog updates its lists |
| Option to skip stop confirmations | ✅ Implemented (preview.2) | Settings › General, or *Don't ask again* in the dialog; covers Force Quit, Stop All and bulk Stop/Restart |
| Taskbar / window icon | ⚠️ Needs real-Windows testing (preview.2) | preview.1 showed no taskbar icon; the .exe icon now uses the classic icon format and the build checks the window has an icon |
| Multi-selection (Ctrl/Shift-click, Ctrl+A), bulk actions | ✅ Implemented | Ctrl instead of ⌘ |
| Staggered launches, configurable launch delay, launch queue | ✅ Implemented | Tested with mock games on Windows |
| Play-time tracking and persistence | ✅ Implemented | Tested on Windows; saves retry if antivirus/OneDrive briefly locks the file |
| Settings persistence across restarts and upgrades | ✅ Implemented | `%APPDATA%`, outside the program folder |
| Crash detection (crash reports, log) | ✅ Implemented | Tested with the mock "Mock Crashy" game on Windows |
| Mock mode (`--mock`) | ✅ Implemented | Smoke-tested in the packaged app on the Windows runner |
| One copy at a time | ✅ Implemented | |
| Prism Launcher discovery | ⚠️ Needs real-Windows testing | Registry (installer), `%LOCALAPPDATA%\Programs\PrismLauncher`, `Program Files`, Scoop, PATH, or choose `prismlauncher.exe` / its folder in Settings; portable Prism supported |
| Launching instances / accounts through Prism | ⚠️ Needs real-Windows testing | Same documented Prism command line as macOS; paths with spaces handled |
| Detecting the running game (`javaw.exe`) | ⚠️ Needs real-Windows testing | Same matching as macOS, with Windows path rules (case, `\` vs `/`) |
| Username / server from the game log | ⚠️ Needs real-Windows testing | Same log parser |
| Live previews | ⚠️ Needs real-Windows testing | `PrintWindow` (works while covered) — verified on the runner with a Qt window, **not with Minecraft's OpenGL window**. If previews are black, switch Settings › Previews › Capture method to *Screen copy* (window must be visible) |
| Bring to Front | ⚠️ Needs real-Windows testing | Restores minimized windows. Windows may refuse to switch focus and flash the taskbar button instead |
| Stop (graceful) | ⚠️ Needs real-Windows testing | Sends the game window a close request (like clicking ✕) so Minecraft saves; verified on the runner with a Qt window |
| Force Quit | ⚠️ Needs real-Windows testing | Ends the process immediately (unsaved progress is lost) |
| Notifications | ⚠️ Needs real-Windows testing | Tray icon + Windows notifications; depends on Windows notification / Do-not-disturb settings |
| Open game folder / crash report | ⚠️ Needs real-Windows testing | Opens File Explorer / the default text editor |
| Previews of minimized windows | ❌ Not possible | Windows doesn't render minimized windows; the last frame is kept |
| Controlling a game started *as administrator* | ❌ Not supported | Windows blocks a normal app from controlling elevated windows; don't run Prism as administrator |
| Windows on ARM build, installer, code signing | ❌ Not yet | x64 zip only; SmartScreen warns on first start |
| Server-disconnect notifications, input forwarding | ❌ Not supported | Same as macOS (see README) |

## Diagnosing problems

- **Log file:** `%LOCALAPPDATA%\MinecraftMultiAccountManager\Logs\app.log`. Crashes are written
  there too.
- **Console version:** the folder also contains **Multi-Account Manager for Minecraft (console).exe**.
  It's the same app but shows the log in a console window, so you can see errors as they happen.
- **Self-check:** in PowerShell, in the app folder:
  `& ".\Multi-Account Manager for Minecraft (console).exe" --self-check`
  prints a report (`"ok": true`, which Prism it found, where your data is).
- **Mock mode** (no Minecraft needed): `& ".\Multi-Account Manager for Minecraft.exe" --mock`
- **Verbose logging:** add `--debug`.

| Problem | Try |
|---|---|
| "Windows protected your PC" | More info › Run anyway (the app isn't code-signed). |
| "Prism Launcher was not found" | Settings › General › App override: choose `prismlauncher.exe` (or its folder). |
| Previews are black | Settings › Previews › Capture method › *Screen copy*, and keep the game windows visible. Report your GPU in the bug report. |
| Previews say "Minimized" | Restore the game window; minimized windows can't be captured. |
| Bring to Front only flashes the taskbar | A Windows focus rule; click the flashing button. Please report it. |
| No notifications | Settings › System › Notifications (allow the app; check Do not disturb). Use *Send Test Notification*. |
| Nothing happens on Launch | Open Prism Launcher and check for sign-in/Java errors; see `prism-cli.log` next to `app.log`. |

## Reporting problems

Open an issue at https://github.com/nicolbolas123/multi-account-manager-for-minecraft/issues
(or DM **nicolbolas123** on Discord) with: Windows version, the app version (Properties › Details of
the .exe), Prism Launcher version, what you did and what happened, and the relevant part of
`app.log` and the `--self-check` output.

**Before posting, remove private information:** the log contains your Minecraft username, server
addresses and folder paths (which include your Windows user name). **Never post anything from
Prism's `accounts.json`**, and never share screenshots of Prism's account screens. The app itself
never reads or stores Microsoft account tokens.

## Building from source (Windows)

Requirements: Windows 10/11 x64, Python 3.11+ 64-bit from
[python.org](https://www.python.org/downloads/windows/) (release builds use 3.14; tick *Add
python.exe to PATH*), Git.

```powershell
git clone https://github.com/nicolbolas123/multi-account-manager-for-minecraft.git
cd multi-account-manager-for-minecraft
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1            # test, build, verify, smoke test
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1 -Package   # ... and dist\release\*.zip
```

The result is `dist\windows\Multi-Account Manager for Minecraft\`. The script uses its own virtual
environment (`build\venv-windows`) with the pinned versions in `packaging\constraints.txt`, runs
the tests, builds with PyInstaller (`packaging\mcmanager_windows.spec`), checks that both
executables are x64 and that the Qt plugins, licenses and version info are included, runs the
app's `--self-check`, and starts the packaged app in mock mode with a throwaway data folder (4
fake games, a simulated crash, a screenshot in `build\windows-smoke\`). Skip parts with
`-SkipTests` / `-SkipSmokeTest`. `$env:PYTHON` selects a specific `python.exe`.

Run from source instead: `python -m venv .venv`, `.venv\Scripts\pip install -r requirements.txt`,
`.venv\Scripts\python -m mcmanager --mock`.

The GitHub Actions workflow **Build Windows app (experimental)** runs the same script on a Windows
runner for every push to the `windows-support` branch and for `v*-windows*` tags, and uploads the
zip and the smoke test's screenshot and log as artifacts. It never publishes a release.

**Build troubleshooting:** "running scripts is disabled" → use the `powershell -ExecutionPolicy
Bypass -File …` form above. "a 64-bit Python is required" → install the 64-bit python.org build.
pip can't find a version → use Python 3.14 (the pinned packages have wheels for it). Antivirus
quarantines files in `dist\` → add an exclusion for the repository folder while building.

## How it works on Windows

| macOS | Windows |
|---|---|
| Quartz window list | `EnumWindows` + `GetWindowThreadProcessId` (visible, unowned, non-tool, non-cloaked windows) |
| ScreenCaptureKit / Quartz capture | `PrintWindow(PW_RENDERFULLCONTENT)`, or a screen copy (`BitBlt`) |
| NSRunningApplication + Accessibility raise | `ShowWindow(SW_RESTORE)` + `SetForegroundWindow` |
| Accessibility close button | `WM_CLOSE` to the game window |
| SIGTERM / SIGKILL | `TerminateProcess` (only after the graceful close isn't possible) |
| UserNotifications | Tray icon notifications (`QSystemTrayIcon.showMessage`) |
| `~/Library/...` | `%APPDATA%`, `%LOCALAPPDATA%` |
| Prism `.app` by bundle ID | `prismlauncher.exe` via registry / install folders / PATH |

All of it is in `mcmanager/windows/win32.py`, `mcmanager/prism/locator.py`,
`mcmanager/notifications.py` and `mcmanager/paths.py`, behind the same interfaces the macOS code
uses. Everything else (UI, settings, groups, launch queue, play time, log parsing, crash detection)
is shared.

---

## Your Windows testing checklist

Use the Windows zip from the preview release. Tick what works; for anything that doesn't, note
what happened and attach the log (see [Reporting problems](#reporting-problems)).

**0. Retest the problems found in preview.1** (preview.2 and later)
- [ ] The taskbar button shows the app icon (also the title bar and Alt+Tab). If you pinned
      preview.1 to the taskbar, unpin it first and pin the new one.
- [ ] Dragging tiles around (within and between groups) never flashes a window.
- [ ] With the dashboard open, add an account in Prism (or copy an instance): within a few seconds
      it's in **+ Add Account**'s lists without restarting. Also with the Add dialog already open.
- [ ] Settings › General › *Ask before stopping or force-quitting games* off: Force Quit and Stop
      All act without asking. *Don't ask again* in the dialog does the same.

**1. Download and start**
- [ ] Download the zip; (optional) its SHA-256 matches the release notes.
- [ ] Extract All; start **Multi-Account Manager for Minecraft.exe**; SmartScreen › More info › Run anyway.
- [ ] The dashboard opens; the window and the taskbar show the app icon; no console window appears.
- [ ] In PowerShell in the folder: `& ".\Multi-Account Manager for Minecraft (console).exe" --self-check` → `"ok": true`.

**2. Prism Launcher detection**
- [ ] Without changing settings, the app finds your Prism (no "not found" banner); Settings › General shows the detected path.
- [ ] Your instances appear (Add All Prism Instances / + Add Account) and the account dropdown lists your Prism accounts.
- [ ] If not found: choosing `prismlauncher.exe` in Settings › General works.

**3. Launch one instance**
- [ ] Launch one account: status goes *Launching…* → *Running*; username and server (if you join one) appear; CPU/RAM shown.

**4. Launch several instances**
- [ ] Launch two or more (different instances): each tile tracks its own game.
- [ ] Stop one: it closes normally (Minecraft saves; tile shows *Stopped*). Force Quit another: it ends.

**5. Live previews and window control**
- [ ] Each running tile shows a live preview of the right game. If black: Settings › Previews › Capture method › *Screen copy* — does that work?
- [ ] Previews keep updating while the game window is covered by another window (PrintWindow mode).
- [ ] Minimize a game: tile says the window is minimized, keeps the last frame; restore: preview resumes.
- [ ] Bring to Front brings the right game window forward (also when minimized).

**6. Status and play time**
- [ ] Play time counts while a game runs; after quitting the game the session is added to the total.
- [ ] Close a game from inside Minecraft (Quit Game): status *Exited* (not *Crashed*).

**7. Groups, reordering, selection, bulk actions**
- [ ] Create a group, drag tiles into it, reorder tiles, pin/collapse the group.
- [ ] Ctrl-click and Shift-click select several tiles; Ctrl+A selects all; Esc / empty-space click clears.
- [ ] Bulk Launch / Stop / Restart Selected work on the selection.

**8. Staggered launches and notifications**
- [ ] Settings › Launching: Staggered Launch on, delay e.g. 30 s; launch several: they start one after another with the progress bar; Cancel Waiting Launches works.
- [ ] Settings › Notifications › Send Test Notification shows a Windows notification.
- [ ] Notifications appear for launch completed / bulk launch / a crash (if you can provoke one).
- [ ] Clicking the tray icon brings the dashboard back.

**9. Persistence**
- [ ] Close the dashboard while a game runs (the game keeps running); reopen: it re-attaches to the running game.
- [ ] Display names, groups, order, favorites, settings and play time are all still there after quitting and reopening.
- [ ] (Later) Replacing the folder with a newer build keeps everything.

**10. Reporting**
- [ ] For each problem: open an issue with Windows version, app version, Prism version, steps, and the relevant part of
      `%LOCALAPPDATA%\MinecraftMultiAccountManager\Logs\app.log` — with your username, server addresses and
      user-folder paths removed. Never include anything from Prism's `accounts.json`.
