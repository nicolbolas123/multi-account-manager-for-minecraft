# Contributing

Thanks for helping! Bug reports, ideas and pull requests are all welcome.

## Reporting a bug

Open an issue at https://github.com/nicolbolas123/multi-account-manager-for-minecraft/issues (or DM **nicolbolas123** on Discord) with:

- what you did, what you expected, and what happened;
- macOS version and Mac type (Apple silicon / Intel), app version (About or `Info.plist`), and
  whether you use the packaged app or run from source;
- Prism Launcher version and, if relevant, the Minecraft version and mod loader;
- the relevant part of `~/Library/Logs/MinecraftMultiAccountManager/app.log`.

**Before attaching logs or screenshots, remove personal information**: logs contain your
Minecraft username, server addresses and local paths (which include your macOS user name).
Never post Prism's `accounts.json` or anything from it. Security problems: see
[SECURITY.md](SECURITY.md) instead of opening a public issue.

## Development setup

Requirements: macOS, Python 3.11+ (from python.org), and Prism Launcher if you want to test with
real games.

```bash
git clone https://github.com/nicolbolas123/multi-account-manager-for-minecraft.git
cd multi-account-manager-for-minecraft
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest          # must pass before a pull request
./run.sh --mock                     # try the UI with simulated instances and games
```

Building the app: see "Build the app yourself" in the [README](README.md#c-build-the-app-yourself).

## Guidelines

- **Keep the layers separate**: Prism discovery/launching (`mcmanager/prism`), runtime state and
  process tracking (`mcmanager/runtime`), macOS integration (`mcmanager/windows`), UI
  (`mcmanager/ui`). The UI must not block on system calls; long work runs on background threads.
- **Safety first with processes**: only ever signal processes positively identified as a game of
  a managed instance (see `process_finder.verify_process`). Never kill unrelated Java processes.
- **Never read or store credentials.** From Prism's data, only instance configuration and account
  *profile names* may be read.
- **User data stays in `~/Library`** (see `mcmanager/paths.py`), never in the repository or the
  app bundle. Keep settings loading tolerant of missing, old or malformed files.
- **Use only documented Prism command-line options** (check `prismlauncher --help`).
- **Add tests** for behaviour changes. Tests run offscreen with mock backends (`tests/`); don't
  require a real Minecraft, Prism or macOS permissions in the test suite.
- Match the surrounding code style: type hints, small modules, comments that explain *why*.
- Don't claim a feature works with real Minecraft unless you tested it with real Minecraft; say
  what was tested in mock mode only.
- Keep pull requests focused; describe what you tested (unit tests, mock mode, real games).
