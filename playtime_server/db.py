"""Postgres access: schema migrations, startup sync of settings, and the queries behind the API."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from psycopg import sql

from .config import Settings

log = logging.getLogger(__name__)

MIGRATIONS = Path(__file__).parent / "migrations"
# Postgres advisory lock id, so two server pods starting together don't both migrate.
_MIGRATION_LOCK = 7_265_001


def utc(ts: int | None) -> datetime | None:
    return datetime.fromtimestamp(ts, timezone.utc) if ts is not None else None


def migrate(conn):
    """Applies migrations/NNN_*.sql that haven't been applied yet, in order, each in its own transaction."""
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_MIGRATION_LOCK,))
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
                     "(name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name in done:
                continue
            log.info("Applying migration %s", path.name)
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))


def sync_settings(conn, settings: Settings):
    """Makes the devices/accounts/settings tables and the Grafana role match the settings."""
    with conn.transaction():
        if not conn.execute("SELECT 1 FROM pg_timezone_names WHERE name = %s", (settings.timezone,)).fetchone():
            raise ValueError(f"TZ: unknown time zone {settings.timezone!r}")
        conn.execute("UPDATE settings SET timezone = %s", (settings.timezone,))
        for device in set(settings.device_token_hashes.values()):
            conn.execute("INSERT INTO devices (name) VALUES (%s) ON CONFLICT DO NOTHING", (device,))
        for account_id, name in settings.accounts.items():
            conn.execute("INSERT INTO accounts VALUES (%s, %s) "
                         "ON CONFLICT (account_id) DO UPDATE SET display_name = EXCLUDED.display_name",
                         (account_id, name))
        if settings.grafana_db_password:
            exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'grafana'").fetchone()
            verb = sql.SQL("ALTER") if exists else sql.SQL("CREATE")
            conn.execute(sql.SQL("{} ROLE grafana LOGIN PASSWORD {}").format(
                verb, sql.Literal(settings.grafana_db_password)))
            conn.execute("GRANT USAGE ON SCHEMA public TO grafana")
            conn.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO grafana")
            conn.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO grafana")


def store_sessions(conn, device: str, sessions: list[dict], current: dict | None, now: datetime) -> list[str]:
    """Inserts new sessions (already-stored uuids are skipped) and records what the device is playing now."""
    with conn.transaction():
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO sessions (uuid, device, account_id, app_id, started_at, ended_at) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (uuid) DO NOTHING",
                [(s["uuid"], device, s["account_id"], s["app_id"], utc(s["started_at"]), utc(s["ended_at"]))
                 for s in sessions])
        conn.execute(
            "UPDATE devices SET last_seen_at = %s, current_account_id = %s, current_app_id = %s, "
            "current_started_at = %s WHERE name = %s",
            (now, current and current["account_id"], current and current["app_id"],
             current and utc(current["started_at"]), device))
    return [s["uuid"] for s in sessions]


def store_totals(conn, device: str, taken_at: int, totals: list[dict], names: dict[int, str]) -> int:
    """Adds the totals that changed since this device's previous report; returns how many were added."""
    with conn.transaction():
        latest = {
            (r[0], r[1]): r[2] for r in conn.execute(
                "SELECT DISTINCT ON (account_id, app_id) account_id, app_id, playtime_minutes "
                "FROM totals_snapshots WHERE device = %s ORDER BY account_id, app_id, taken_at DESC", (device,))
        }
        changed = [t for t in totals if latest.get((t["account_id"], t["app_id"])) != t["playtime_minutes"]]
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO totals_snapshots (device, account_id, app_id, playtime_minutes, last_played, taken_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                [(device, t["account_id"], t["app_id"], t["playtime_minutes"], utc(t.get("last_played")),
                  utc(taken_at)) for t in changed])
            cur.executemany(
                "INSERT INTO games (app_id, name, source) VALUES (%s, %s, 'manifest') "
                "ON CONFLICT (app_id) DO UPDATE SET name = EXCLUDED.name, source = 'manifest', fetched_at = now()",
                list(names.items()))
        conn.execute("UPDATE devices SET last_seen_at = now() WHERE name = %s", (device,))
    return len(changed)


def apps_missing_names(conn, limit: int) -> list[int]:
    """Apps seen in sessions or totals with no name yet (unknown ones are retried after 30 days)."""
    rows = conn.execute(
        """
        SELECT app_id FROM (SELECT DISTINCT app_id FROM sessions
                            UNION SELECT DISTINCT app_id FROM totals_snapshots) seen
        LEFT JOIN games g USING (app_id)
        WHERE g.app_id IS NULL OR (g.name IS NULL AND g.fetched_at < now() - interval '30 days')
        ORDER BY app_id LIMIT %s
        """, (limit,))
    return [r[0] for r in rows]


def store_game_name(conn, app_id: int, name: str | None):
    with conn.transaction():
        conn.execute(
            "INSERT INTO games (app_id, name, source) VALUES (%s, %s, 'store') "
            "ON CONFLICT (app_id) DO UPDATE SET name = COALESCE(EXCLUDED.name, games.name), fetched_at = now() "
            "WHERE games.source = 'store'", (app_id, name))
