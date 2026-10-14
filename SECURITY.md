# Security policy

## Reporting a vulnerability

Please **don't open a public issue** for security problems. Report them privately through
GitHub: **Security › Report a vulnerability** on [the repository](https://github.com/nicolbolas123/multi-account-manager-for-minecraft/security) (GitHub private vulnerability
reporting). Include what is affected, how to reproduce it, and the impact you expect. You'll get
an acknowledgement as soon as possible; please allow time for a fix before disclosing publicly.

## Supported versions

Only the latest release (and the current `main` branch) receive security fixes.

## Scope and design notes

Relevant areas include anything that could:

- expose Minecraft/Microsoft account data (the app must never read or store tokens; it reads only
  account profile names from Prism's `accounts.json`);
- send signals to, or otherwise affect, processes other than the managed Minecraft games;
- write user data outside `~/Library/…/MinecraftMultiAccountManager` or into the app bundle;
- run commands with untrusted input (e.g. notification text is passed to `osascript` as
  arguments, never interpolated into a script).

The app makes no network connections of its own. It relies on macOS permissions (Screen
Recording, Accessibility, Notifications) and never tries to bypass them.

**Downloaded builds** are currently not signed with an Apple Developer ID or notarized, so macOS
can't verify who built them. Only install builds from this repository's Releases page, and
consider building from source (see the README) if you prefer to verify what you run.
