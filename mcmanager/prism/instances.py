"""Read-only listing of Prism instances and accounts.

We never write to Prism's files. Account information is limited to profile
names; tokens in ``accounts.json`` are not read into our data structures.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from mcmanager.prism.cfgparse import read_cfg

log = logging.getLogger(__name__)

LOADER_NAMES = {
    "net.fabricmc.fabric-loader": "Fabric",
    "org.quiltmc.quilt-loader": "Quilt",
    "net.minecraftforge": "Forge",
    "net.neoforged": "NeoForge",
    "com.mumfrey.liteloader": "LiteLoader",
}


@dataclass(frozen=True)
class PrismInstance:
    id: str  # folder name; this is what ``--launch`` expects
    name: str
    path: Path
    minecraft_version: str | None = None
    loader: str | None = None
    group: str | None = None
    # Prism's own per-instance play-time counters (instance.cfg). Per instance, not per account;
    # only updated for launches Prism itself tracked. Shown for reference, not used for statistics.
    prism_total_played_s: int | None = None
    prism_last_launch: float | None = None  # epoch seconds

    @property
    def game_dir(self) -> Path:
        for sub in ("minecraft", ".minecraft"):
            candidate = self.path / sub
            if candidate.is_dir():
                return candidate
        return self.path / "minecraft"

    @property
    def natives_dir(self) -> Path:
        return self.path / "natives"

    @property
    def summary(self) -> str:
        parts = [p for p in (self.minecraft_version, self.loader) if p]
        return " · ".join(parts)


@dataclass(frozen=True)
class PrismAccount:
    profile_name: str
    account_type: str
    active: bool


def _read_pack(path: Path) -> tuple[str | None, str | None]:
    try:
        data = json.loads((path / "mmc-pack.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    version = loader = None
    for comp in data.get("components", []):
        uid = comp.get("uid")
        if uid == "net.minecraft":
            version = comp.get("version") or comp.get("cachedVersion")
        elif uid in LOADER_NAMES:
            loader = LOADER_NAMES[uid]
    return version, loader


def _read_groups(instances_dir: Path) -> dict[str, str]:
    try:
        data = json.loads((instances_dir / "instgroups.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    groups: dict[str, str] = {}
    for group_name, group in (data.get("groups") or {}).items():
        for inst_id in group.get("instances", []):
            groups[inst_id] = group_name
    return groups


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


def list_instances(instances_dir: Path) -> list[PrismInstance]:
    if not instances_dir.is_dir():
        return []
    groups = _read_groups(instances_dir)
    result = []
    for child in sorted(instances_dir.iterdir()):
        cfg_path = child / "instance.cfg"
        if not child.is_dir() or child.name.startswith(".") or not cfg_path.is_file():
            continue
        cfg = read_cfg(cfg_path)
        version, loader = _read_pack(child)
        result.append(
            PrismInstance(
                id=child.name,
                name=cfg.get("name") or child.name,
                path=child,
                minecraft_version=version,
                loader=loader,
                group=groups.get(child.name),
                prism_total_played_s=_int_or_none(cfg.get("totalTimePlayed")),
                prism_last_launch=(_int_or_none(cfg.get("lastLaunchTime")) or 0) / 1000 or None,
            )
        )
    return result


def list_accounts(data_dir: Path) -> list[PrismAccount]:
    """Return account profile names known to Prism (no credentials)."""
    try:
        data = json.loads((data_dir / "accounts.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    accounts = []
    for raw in data.get("accounts", []):
        profile = raw.get("profile") or {}
        name = profile.get("name")
        if name:
            accounts.append(
                PrismAccount(profile_name=name, account_type=str(raw.get("type", "")), active=bool(raw.get("active")))
            )
    return accounts
