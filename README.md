# Playlog: Steam playtime tracker

Records every Steam session on your Windows PC and Steam Deck, for every account signed in there, and keeps the full
history in Postgres, with Grafana dashboards. Steam itself only keeps lifetime totals, and its Web API returns nothing
for private profiles, so nothing here talks to Steam's servers: an agent on each device reads Steam's local state once
a minute.

```
 PC (Task Scheduler, pythonw)  ─┐   POST /api/v1/sync    (sessions + what's playing now)
                                ├──────────────────────▶  playtime server ──▶ Postgres ◀── Grafana (dashboard)
 Deck (systemd user service)  ──┘   POST /api/v1/totals  (lifetime totals, daily)   └── /metrics ◀── Prometheus
```

| Path | What |
|---|---|
| `playtime_agent/` | The agent, standard library only (same code on Windows and the Deck) |
| `playtime_server/` | FastAPI server: uploads, game names, untracked-time catch-up, `/metrics`; SQL migrations |
| `dashboards/` | Grafana dashboards: "Steam playtime" (overview, heatmaps) and "Steam sessions" (every session on a timeline) |
| `install/windows/`, `install/deck/` | Agent install scripts |
| `tests/` | `python -m unittest discover -s tests` |

## How it works

- **Sessions.** Every 60 s the agent reads the signed-in account and running game (Windows: registry
  `HKCU\Software\Valve\Steam`, `RunningAppID` and `ActiveProcess\ActiveUser`; Deck: `~/.steam/registry.vdf`, and the
  game's `SteamLaunch AppId=` process). One record per session; a gap over 3 minutes (sleep) ends it. Finished sessions
  wait in a small SQLite queue until the server confirms them, so the Deck can be offline for months.
- **Lifetime totals.** Once a day the agent sends each account's `userdata/<id>/config/localconfig.vdf` playtimes
  (only changes are stored), plus names of installed games. The first upload imports the history up to now.
- **Untracked time.** When a lifetime total grows more than the tracked sessions explain, the difference is shown as
  device "other" (another device, or before the agent existed). Recomputed after every upload, so Deck sessions that
  arrive months later take that time back. Totals sync across devices and are never added together.
- **Game names** come from installed games' manifests, otherwise from the public Steam store page (cached).
- **Account names** come from the server's `ACCOUNTS` setting.

## Development

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt     # Linux: .venv/bin/pip
python -m playtime_agent --check          # what the agent sees on this machine (records nothing)
.venv/Scripts/python -m unittest discover -s tests
```

**Local preview** of the server side (Postgres, server and Grafana with the dashboard) with Docker:
```bash
docker compose -f dev/docker-compose.yml up -d --build    # Grafana: http://localhost:3000 (TZ=..., ACCOUNTS=... optional)
python dev/seed_demo.py                                    # your real lifetime totals + made-up sample sessions
docker compose -f dev/docker-compose.yml down -v           # throw it all away
```

The server and database tests need a throwaway Postgres in `TEST_DATABASE_URL`, e.g.
`postgresql://postgres:test@localhost:5432/postgres`. Every test wipes its `public` schema. Without it they're
skipped. CI runs them against `postgres:17`.

## Deploying the server

The server is the container built by the `Dockerfile` (it listens on port 8080 and creates its tables on start):
```bash
docker build -t playlog-server:0.2.0 .
```
Run it next to a Postgres 17 database with these environment variables (see `playtime_server/config.py`):

| Variable | Example | |
|---|---|---|
| `DATABASE_URL` | `postgresql://playtime:<password>@postgres:5432/playtime` | |
| `DEVICE_TOKENS` | `pc=<openssl rand -hex 24>,deck=<openssl rand -hex 24>` | secret; one per agent, the names label the devices |
| `ACCOUNTS` | `12345678=Alice,87654321=Bob` | Steam account IDs -> names (`python -m playtime_agent --check` prints them) |
| `TZ` | `Europe/Berlin` | days and hours on the dashboards (default UTC) |
| `GRAFANA_DB_PASSWORD` | `<random>` | optional: creates a read-only `grafana` role for the datasource |

Put it behind HTTPS reachable from your devices (the agents need `/api/v1/*`; `/metrics` is for Prometheus). For the
dashboards, add a Grafana **PostgreSQL** datasource with uid `steam-playtime` (user `grafana`, database `playtime`) and
import `dashboards/*.json`; `dev/` shows a complete example with Docker Compose. In `dashboards/build.py`,
`PRIMARY_ACCOUNT` is the account name shown in the first color.

## Installing the agents

**PC** (no admin; Python 3.10+ from python.org on PATH), from this repo:
```powershell
powershell -ExecutionPolicy Bypass -File install\windows\install.ps1 -ServerUrl https://<your server> -Token <pc token>
```
The agent runs hidden at every logon (Task Scheduler: "Steam Playtime Agent"). Its log is
`%LOCALAPPDATA%\SteamPlaytime\agent.log`. To update, re-run the script. `-Uninstall` removes it.

**Steam Deck** (Desktop Mode, Konsole; nothing to install, since SteamOS ships `python3`):
```bash
git clone https://github.com/QuinnBast/Playlog.git ~/steam-playtime-src   # or copy the folder over
~/steam-playtime-src/install/deck/install.sh --server https://<your server> --token <deck token>
```
It runs as a systemd user service in both Desktop and Gaming Mode, and survives SteamOS updates because it lives in
the home folder. Check it with `systemctl --user status steam-playtime`. The log is
`~/.local/share/steam-playtime/agent.log`.
To update: `git -C ~/steam-playtime-src pull` and re-run `install.sh` (no server or token needed the second time).

Before relying on the Deck data, play something for a few minutes and check that the session shows up. Steam may be
slow to rewrite `registry.vdf`, which is why the game's process is checked too.

## Notes

- The agent records whichever Steam account is signed in, so let anyone else who uses the device know before it
  goes live.
- "Owned but never played" isn't possible offline: Steam keeps no readable list of owned games locally, and the
  Steam Web API won't return one for a private profile. The lifetime table lists every game that has playtime.
- Non-Steam games are ignored.
