"""Fills in game names for apps no device has installed, from the Steam store's public (keyless) app details.

Names from a device's installed games arrive with its totals; this covers the rest. Slow on purpose: the store
endpoint rate-limits (roughly 200 requests per 5 minutes).
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.request

from . import db

log = logging.getLogger(__name__)

STORE_URL = "https://store.steampowered.com/api/appdetails?filters=basic&appids={}"


def fetch_store_name(app_id: int, timeout: float = 15) -> str | None:
    """The store's name for an app, None if the store has no page for it. Network errors raise."""
    request = urllib.request.Request(STORE_URL.format(app_id), headers={"User-Agent": "steam-playtime-tracker"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        entry = json.loads(response.read()).get(str(app_id)) or {}
    if not entry.get("success"):
        return None
    name = (entry.get("data") or {}).get("name")
    return name if isinstance(name, str) and name.strip() else None


BATCH = 50


def fill_missing_names(pool, fetch=fetch_store_name, batch: int = BATCH, pause: float = 2.0, stop=None) -> int:
    """Looks up to `batch` missing names; returns how many lookups succeeded (found or not)."""
    stop = stop or threading.Event()
    with pool.connection() as conn:
        missing = db.apps_missing_names(conn, batch)
    looked_up = filled = 0
    for app_id in missing:
        if stop.is_set():
            break
        try:
            name = fetch(app_id)
        except Exception as e:  # network trouble: try again next round
            log.warning("Store lookup for app %s failed: %s", app_id, e)
            break
        with pool.connection() as conn:
            db.store_game_name(conn, app_id, name)
        looked_up += 1
        filled += name is not None
        stop.wait(pause)
    if filled:
        log.info("Fetched %d game names from the Steam store", filled)
    return looked_up


def run_forever(pool, stop: threading.Event, wake: threading.Event, every: float = 600):
    """Looks up missing names every `every` seconds, and soon after `wake` is set (new apps uploaded)."""
    while not stop.is_set():
        wake.clear()
        try:
            if fill_missing_names(pool, stop=stop) == BATCH:
                continue  # a full batch went through: probably more waiting (e.g. the first import)
        except Exception:
            log.exception("Game name lookup failed")
        wake.wait(every)
