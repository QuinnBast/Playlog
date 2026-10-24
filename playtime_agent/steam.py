"""Reads Steam's local files: lifetime playtime per account (localconfig.vdf) and installed game names (appmanifests).

The same layout on Windows and the Deck; only the Steam folder differs.
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import vdf

log = logging.getLogger(__name__)

# SteamID64 = this + the 32-bit account ID (what the registry, userdata/ folders and the server use).
STEAMID64_BASE = 76561197960265728


def _int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def lifetime_totals(steam_root: Path) -> list[dict]:
    """Lifetime playtime of every game in every account's localconfig.vdf on this device."""
    totals = []
    for user_dir in sorted((steam_root / "userdata").glob("*")):
        account_id = _int(user_dir.name)
        config = user_dir / "config" / "localconfig.vdf"
        if account_id <= 0 or not config.is_file():
            continue
        try:
            apps = vdf.get_path(vdf.load(config), "UserLocalConfigStore", "Software", "Valve", "Steam", "apps")
        except (OSError, vdf.VdfError) as e:
            log.warning("Can't read %s: %s", config, e)
            continue
        for app_id, fields in (apps or {}).items():
            if not isinstance(fields, dict) or _int(app_id) <= 0:
                continue
            minutes = _int(vdf.get_path(fields, "Playtime"), -1)
            if minutes < 0:
                continue  # cloud-sync-only entries have no playtime
            totals.append({
                "account_id": account_id,
                "app_id": int(app_id),
                "playtime_minutes": minutes,
                "last_played": _int(vdf.get_path(fields, "LastPlayed")) or None,
            })
    return totals


def library_folders(steam_root: Path) -> list[Path]:
    folders = [steam_root]
    try:
        data = vdf.load(steam_root / "steamapps" / "libraryfolders.vdf")
    except (OSError, vdf.VdfError):
        return folders
    for entry in (vdf.get_path(data, "libraryfolders") or {}).values():
        path = vdf.get_path(entry, "path")
        if isinstance(path, str) and path:
            p = Path(path)
            if p.resolve() != steam_root.resolve() and p not in folders:
                folders.append(p)
    return folders


def installed_game_names(steam_root: Path) -> dict[int, str]:
    names = {}
    for folder in library_folders(steam_root):
        for manifest in (folder / "steamapps").glob("appmanifest_*.acf"):
            try:
                state = vdf.get_path(vdf.load(manifest), "AppState") or {}
            except (OSError, vdf.VdfError):
                continue
            app_id, name = _int(vdf.get_path(state, "appid")), vdf.get_path(state, "name")
            if app_id > 0 and isinstance(name, str) and name:
                names[app_id] = name
    return names
