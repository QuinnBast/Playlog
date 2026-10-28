# Playlog: Open-Source Personal Steam playtime tracker

Playtime is an open-source self-hostable tool that allows you to track every Steam session on your Windows PC and Steam Deck, for every account.
The self-hosted server keeps track of your play history in Postgres and there are included Grafana dashboards for viewing your stats.

Two daemon applications exist, one for Windows, one for Steam Deck (linux). The Daemon processes activate on system startup
and watch for playtime. Once a session has been tracked, the daemons attempt to upload to your configured server URL.

Not online? No problem! The daemon footprint is small. It will keep playtime sessions locally until you're back online and upload all previous
sessions once you're able to connect to your local server.

Nothing here talks to Steam's servers, everything runs entirely locally.

```
 PC (Task Scheduler, pythonw)  ─┐   POST /api/v1/sync
                                ├──────────────────────▶  playtime server ──▶ Postgres ◀── Grafana (dashboard)
 Deck (systemd user service)  ──┘   POST /api/v1/totals            └── /metrics ◀── Prometheus
```

# Get started

## [Deploy the Server](#deploying-the-server)

## [Install an agent](#installing-the-agents)

## How it works

- **Sessions.** Every 60 s the agent reads the signed-in Steam account and running game.
  - Windows reads registry `HKCU\Software\Valve\Steam`, `RunningAppID` and `ActiveProcess\ActiveUser`.
  - Deck: reads `~/.steam/registry.vdf`, and the game's `SteamLaunch AppId=` process.
  - Finished sessions wait in a small SQLite queue until the server confirms them, so the Deck can be offline for months.
- **Lifetime totals.** The first time you run, it uploads your steam lifetime totals for each game to the server to build on.
- **Game names** come from installed games' manifests, otherwise from the public Steam store page (cached).
- **Account names** are defined by you, configure in the server's `ACCOUNTS` setting.

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

I do not have a DockerHub account and have no registry to host container builds at.
So to get the server, you will need to build the container yourself (sorry).

The server is the container built by the `Dockerfile` at the root of this repo (it listens on port 8080 and creates its Postgres tables on start).
Build the container with:

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

`DEVICE_TOKENS` are secret tokens that the server will only accept. The server requires a token on request, and maps the token to a device.
Treat these like API keys.

Once running, ensure it is reachable on your network so that the agents can reach it.

For the dashboards, add a Grafana **PostgreSQL** datasource with uid `steam-playtime` (user `grafana`, database `playtime`).
Then import `dashboards/*.json`.

The `dev/` folder shows a complete example using Docker Compose.
In `dashboards/build.py`, `PRIMARY_ACCOUNT` is the account name shown in the first color.

## Installing the agents

**PC** (Python 3.10+ required)

Install on PC by running the following command:
```powershell
powershell -ExecutionPolicy Bypass -File install\windows\install.ps1 -ServerUrl https://<your server> -Token <pc token>
```
Once executed, the daemon will run automatically at every logon.
You can view (or cancel) the process under Task Scheduler: "Steam Playtime Agent".
Its log is found at `%LOCALAPPDATA%\SteamPlaytime\agent.log`.
To update, re-run the script. `-Uninstall` removes it.

**Steam Deck** (Requires Desktop Mode via Konsole):

Run the following commands on your Steam Deck:

```bash
git clone https://github.com/QuinnBast/Playlog.git ~/steam-playtime-src   # or copy the folder over
~/steam-playtime-src/install/deck/install.sh --server https://<your server> --token <deck token>
```

It runs as a systemd user service in both Desktop and Gaming Mode, and survives SteamOS updates because it lives in  the home folder.
Check its process with `systemctl --user status steam-playtime`.
The log is located at `~/.local/share/steam-playtime/agent.log`.
To update: `git -C ~/steam-playtime-src pull` and re-run `install.sh` (no server or token needed the second time).

## Notes

- The agent records any Steam account that is signed in.
- Non-Steam games are ignored (for now?).
