"""Fills the dev stack with sample data so the dashboard has something to show.

- Your real lifetime totals and installed game names, read and uploaded by the real agent code.
- A year of made-up sessions (your most-played local account on PC and Deck, plus a made-up "Sample partner" account
  12345, mostly on the Deck), uploaded through the real API. The partner's totals grow more than its sessions explain,
  which shows up as
  untracked "other" time.

    python dev/seed_demo.py            (after: docker compose -f dev/docker-compose.yml up -d --build)
Dev stack only - never point this at the real server.
"""

import random
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playtime_agent import platforms, steam  # noqa: E402
from playtime_agent.uploader import Uploader  # noqa: E402

SERVER = "http://localhost:8080"
PC = Uploader(SERVER, "dev-pc-token-0123456789")
DECK = Uploader(SERVER, "dev-deck-token-0123456789")
PARTNER = 12345
DAY = 86400

random.seed(7)
now = int(time.time())

# Real totals from this PC's Steam.
root = platforms.current().steam_root()
totals = steam.lifetime_totals(root)
PC.totals(totals, steam.installed_game_names(root), now)
print(f"Uploaded {len(totals)} real lifetime totals from {root}")

# Your account: the one with the most playtime in this PC's Steam folder.
by_account = {}
for t in totals:
    by_account[t["account_id"]] = by_account.get(t["account_id"], 0) + t["playtime_minutes"]
ME = max(by_account, key=by_account.get)
print(f"Your account: {ME} (set ACCOUNTS={ME}=<your name>,12345=Sample partner to name it on the dashboards)")
mine = [t["app_id"] for t in sorted(totals, key=lambda t: -t["playtime_minutes"]) if t["account_id"] == ME][:12]
partner_games = {413150: "Stardew Valley", 1868140: "Dave the Diver", 504230: "Celeste", 1145360: "Hades",
                 1794680: "Vampire Survivors", 367520: "Hollow Knight"}


def sessions_for(account, games, days, per_day, deck_share):
    by_device = {"pc": [], "deck": []}
    for d in range(days, 0, -1):
        day_start = now - d * DAY - (now % DAY) + 6 * 3600   # roughly local midnight (UTC-6)
        for _ in range(random.choice(per_day)):
            start = day_start + random.randint(17, 22) * 3600 + random.randint(0, 3599)
            if random.random() < 0.25:
                start -= random.randint(5, 9) * 3600          # some daytime play
            length = int(random.triangular(15, 240, 70) * 60)
            device = "deck" if random.random() < deck_share else "pc"
            game = random.choices(games, weights=range(len(games), 0, -1))[0]
            by_device[device].append({"uuid": str(uuid.uuid4()), "account_id": account, "app_id": game,
                                      "started_at": start, "ended_at": start + length})
    return by_device


mine_s = sessions_for(ME, mine, 365, [0, 1, 1, 2, 2, 3], 0.3)
partner_s = sessions_for(PARTNER, list(partner_games), 365, [0, 0, 1, 1, 2], 0.85)

# Partner: lifetime totals before tracking began, then today's totals = before + sessions + some untracked play.
before = {g: random.randint(300, 6000) for g in partner_games}
partner_minutes = {g: 0 for g in partner_games}
for s in partner_s["pc"] + partner_s["deck"]:
    partner_minutes[s["app_id"]] += (s["ended_at"] - s["started_at"]) // 60
DECK.totals([{"account_id": PARTNER, "app_id": g, "playtime_minutes": m} for g, m in before.items()],
            partner_games, now - 366 * DAY)
extra = {413150: 540, 1145360: 300}   # untracked: e.g. played on a laptop without an agent
DECK.totals([{"account_id": PARTNER, "app_id": g, "playtime_minutes": before[g] + partner_minutes[g] + extra.get(g, 0)}
             for g in partner_games], partner_games, now - 60)

for uploader, device in ((PC, "pc"), (DECK, "deck")):
    batch = mine_s[device] + partner_s[device]
    uploader.sync(batch, None, now)
    print(f"Uploaded {len(batch)} sample sessions as {device}")

# Something playing right now, for the "Now playing" panel.
PC.sync([], {"uuid": str(uuid.uuid4()), "account_id": ME, "app_id": mine[0], "started_at": now - 1800,
             "ended_at": now}, now)
print("Done: http://localhost:3000")
