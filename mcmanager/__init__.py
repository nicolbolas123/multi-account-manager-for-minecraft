"""Multi-Account Manager for Minecraft.

A desktop dashboard that launches Minecraft instances through Prism Launcher,
tracks the resulting game processes and shows live previews of their windows.

Package layout:
    config/   persisted user settings and dashboard entries
    prism/    Prism Launcher discovery, instance/account listing, launching
    runtime/  process tracking, log parsing and the per-entry state machine
    windows/  window discovery, live capture, focusing (macOS)
    mock/     clearly separated mock environment for demos and testing
    ui/       PySide6 interface
"""

APP_NAME = "Multi-Account Manager for Minecraft"
# The two identifiers below predate the current name and are kept on purpose: APP_ID names the
# user-data folders in ~/Library (renaming it would orphan existing settings and play time), and
# the bundle identifier is what macOS ties permissions to.
APP_ID = "MinecraftMultiAccountManager"  # folder name for user data, logs and caches
APP_BUNDLE_ID = "local.MinecraftMultiAccountManager"  # macOS bundle identifier of the packaged .app

GITHUB_REPO = "nicolbolas123/multi-account-manager-for-minecraft"
ISSUES_URL = f"https://github.com/{GITHUB_REPO}/issues"
DISCORD_USERNAME = "nicolbolas123"
__version__ = "0.1.0"
