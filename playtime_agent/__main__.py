"""Entry point: python -m playtime_agent [--config PATH] [--check]

Config (JSON; default: %APPDATA%\\SteamPlaytime\\config.json or ~/.config/steam-playtime/config.json):
  {"server_url": "https://playlog.example.lan", "device": "pc", "token": "<this device's token>",
   "steam_path": null, "interval_seconds": 60}
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import sys
import time
from pathlib import Path

from . import platforms, steam
from .agent import Agent
from .store import Store
from .uploader import Uploader


def check(source, root: Path | None):
    """Prints what the agent can see, without recording or uploading anything."""
    account_id, app_id = source.running()
    print(f"Steam folder:   {root}")
    print(f"Signed-in user: {account_id} (SteamID64 {steam.STEAMID64_BASE + account_id if account_id else '-'})")
    print(f"Running game:   {app_id or '-'}")
    if root:
        totals = steam.lifetime_totals(root)
        names = steam.installed_game_names(root)
        for acc in sorted({t["account_id"] for t in totals}):
            mine = [t for t in totals if t["account_id"] == acc]
            top = sorted(mine, key=lambda t: -t["playtime_minutes"])[:5]
            print(f"Account {acc}: {len(mine)} games, {sum(t['playtime_minutes'] for t in mine) / 60:.0f} h total; top:")
            for t in top:
                print(f"    {t['playtime_minutes'] / 60:7.1f} h  {names.get(t['app_id'], t['app_id'])}")
        print(f"Installed games with names: {len(names)}")


def main(argv=None):
    config_dir, data_dir = platforms.default_dirs()
    parser = argparse.ArgumentParser(prog="playtime_agent")
    parser.add_argument("--config", type=Path, default=config_dir / "config.json")
    parser.add_argument("--check", action="store_true", help="print what Steam reports and exit")
    args = parser.parse_args(argv)

    source = platforms.current()
    config = json.loads(args.config.read_text()) if args.config.is_file() else {}
    root = Path(config["steam_path"]) if config.get("steam_path") else source.steam_root()
    if args.check:
        check(source, root)
        return

    if not config.get("server_url") or not config.get("token"):
        sys.exit(f"Missing server_url/token in {args.config}")
    data_dir.mkdir(parents=True, exist_ok=True)
    handlers = [logging.handlers.RotatingFileHandler(data_dir / "agent.log", maxBytes=256_000, backupCount=1)]
    if sys.stderr:  # pythonw has no console
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)

    interval = int(config.get("interval_seconds", 60))
    agent = Agent(source, Store(data_dir / "queue.db"), Uploader(config["server_url"], config["token"]),
                  steam_root=root)
    logging.info("Agent started (device %s, Steam folder %s)", config.get("device"), root)
    while True:
        try:
            agent.tick()
        except Exception:
            logging.exception("Tick failed")
        time.sleep(interval - time.time() % interval)


if __name__ == "__main__":
    main()
