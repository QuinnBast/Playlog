"""Server tests. The ones touching the database need a throwaway Postgres in TEST_DATABASE_URL (its public schema
is wiped for every test); without it they're skipped. CI provides one."""

import contextlib
import os
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone

try:
    import fastapi  # noqa: F401  (server dependencies are optional for agent-only runs)
    HAVE_SERVER_DEPS = True
except ImportError:
    HAVE_SERVER_DEPS = False

if HAVE_SERVER_DEPS:
    import psycopg
    from fastapi.testclient import TestClient
    from psycopg_pool import ConnectionPool

    from playtime_server import catchup, names
    from playtime_server.app import SessionIn, create_app, session_problem
    from playtime_server.config import Settings

DB_URL = os.environ.get("TEST_DATABASE_URL")
T0 = int(time.time()) - 30 * 86400   # the server rejects sessions in the future
PC_TOKEN, DECK_TOKEN = "pc-token-0123456789abcdef", "deck-token-0123456789abcdef"


@unittest.skipUnless(HAVE_SERVER_DEPS, "server dependencies not installed")
class SettingsTest(unittest.TestCase):
    def test_from_env(self):
        s = Settings.from_env({"DATABASE_URL": "postgresql://x", "DEVICE_TOKENS": f"pc={PC_TOKEN}, deck={DECK_TOKEN}",
                               "ACCOUNTS": "12345678=Alice"})
        self.assertEqual(sorted(s.device_token_hashes.values()), ["deck", "pc"])
        self.assertEqual(s.accounts, {12345678: "Alice"})
        self.assertTrue(s.fetch_game_names)

    def test_rejects_missing_or_short_tokens(self):
        for tokens in ["", "pc=short", "pc"]:
            with self.subTest(tokens=tokens), self.assertRaises(ValueError):
                Settings.from_env({"DATABASE_URL": "postgresql://x", "DEVICE_TOKENS": tokens})


@unittest.skipUnless(HAVE_SERVER_DEPS, "server dependencies not installed")
class SessionValidationTest(unittest.TestCase):
    def session(self, **changes):
        fields = dict(uuid=str(uuid.uuid4()), account_id=1, app_id=400, started_at=T0, ended_at=T0 + 600)
        fields.update(changes)
        return SessionIn(**fields)

    def test_valid(self):
        self.assertIsNone(session_problem(self.session(), T0 + 700))
        self.assertIsNone(session_problem(self.session(ended_at=T0), T0))

    def test_invalid(self):
        for changes in [dict(uuid="nope"), dict(account_id=0), dict(app_id=2**31), dict(ended_at=T0 - 1),
                        dict(ended_at=T0 + 8 * 86400), dict(started_at=T0 + 2 * 86400, ended_at=T0 + 2 * 86400)]:
            with self.subTest(**changes):
                self.assertIsNotNone(session_problem(self.session(**changes), T0))


@unittest.skipUnless(HAVE_SERVER_DEPS, "server dependencies not installed")
class StoreNameParsingTest(unittest.TestCase):
    def test_fill_missing_names_stops_on_network_errors(self):
        class FakePool:
            def connection(self):
                return contextlib.nullcontext()

        calls = []

        def fetch(app_id):
            calls.append(app_id)
            raise OSError("offline")

        original = names.db.apps_missing_names
        names.db.apps_missing_names = lambda conn, limit: [10, 20]
        try:
            self.assertEqual(names.fill_missing_names(FakePool(), fetch=fetch, pause=0), 0)
        finally:
            names.db.apps_missing_names = original
        self.assertEqual(calls, [10])


@unittest.skipUnless(HAVE_SERVER_DEPS and DB_URL, "needs TEST_DATABASE_URL")
class ApiTest(unittest.TestCase):
    def setUp(self):
        with psycopg.connect(DB_URL, autocommit=True) as conn:
            conn.execute("DROP SCHEMA public CASCADE")
            conn.execute("CREATE SCHEMA public")
        self.settings = Settings.from_env({
            "DATABASE_URL": DB_URL, "DEVICE_TOKENS": f"pc={PC_TOKEN},deck={DECK_TOKEN}",
            "ACCOUNTS": "111=Alice,222=Bob", "GRAFANA_DB_PASSWORD": "grafana-test-password",
            "TZ": "Etc/GMT+6"})   # UTC-6 all year (POSIX sign: + is west)
        self.pool = ConnectionPool(DB_URL, kwargs={"autocommit": True}, min_size=1, max_size=2, open=False)
        self.client = TestClient(create_app(self.settings, self.pool, background=False))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def query(self, sql, *params):
        with self.pool.connection() as conn:
            return conn.execute(sql, params).fetchall()

    def post(self, path, body, token=PC_TOKEN):
        return self.client.post(path, json=body, headers={"Authorization": f"Bearer {token}"})

    def session(self, account=111, app=400, start=T0, minutes=30):
        return {"uuid": str(uuid.uuid4()), "account_id": account, "app_id": app,
                "started_at": start, "ended_at": start + minutes * 60}

    def test_requires_a_known_token(self):
        self.assertEqual(self.client.post("/api/v1/sync", json={"sent_at": T0}).status_code, 401)
        self.assertEqual(self.post("/api/v1/sync", {"sent_at": T0}, token="wrong-token-0123456789").status_code, 401)

    def test_sessions_are_stored_once(self):
        a, b = self.session(), self.session(account=222, start=T0 + 3600)
        reply = self.post("/api/v1/sync", {"sent_at": T0, "sessions": [a, b]})
        self.assertEqual(reply.status_code, 200, reply.text)
        self.assertEqual(sorted(reply.json()["stored"]), sorted([a["uuid"], b["uuid"]]))
        self.assertEqual(self.post("/api/v1/sync", {"sent_at": T0, "sessions": [a]}).json()["stored"], [a["uuid"]])
        rows = self.query("SELECT account, device, seconds FROM v_sessions ORDER BY started_at")
        self.assertEqual(rows, [("Alice", "pc", 1800), ("Bob", "pc", 1800)])

    def test_invalid_sessions_are_acknowledged_but_not_stored(self):
        bad = self.session()
        bad["ended_at"] = bad["started_at"] - 1
        self.assertEqual(self.post("/api/v1/sync", {"sent_at": T0, "sessions": [bad]}).json()["stored"], [bad["uuid"]])
        self.assertEqual(self.query("SELECT count(*) FROM sessions"), [(0,)])

    def test_current_game_and_metrics(self):
        current = self.session(app=570, minutes=5)
        self.post("/api/v1/totals", {"taken_at": T0, "totals": [], "games": {"570": "Dota 2"}})
        self.post("/api/v1/sync", {"sent_at": T0, "sessions": [self.session(minutes=60)], "current": current})
        text = self.client.get("/metrics").text
        self.assertIn('steam_playtime_now_playing{account="Alice",app_id="570",device="pc",game="Dota 2"} 1.0', text)
        self.assertIn('steam_playtime_tracked_seconds_total{account="Alice",device="pc"} 3600.0', text)
        self.assertIn('steam_playtime_device_last_seen_timestamp_seconds{device="pc"}', text)
        self.post("/api/v1/sync", {"sent_at": T0, "current": None})
        self.assertNotIn("steam_playtime_now_playing{", self.client.get("/metrics").text)

    def test_totals_store_only_changes_and_names(self):
        totals = [{"account_id": 111, "app_id": 400, "playtime_minutes": 600, "last_played": T0},
                  {"account_id": 222, "app_id": 400, "playtime_minutes": 5}]
        body = {"taken_at": T0, "totals": totals, "games": {"400": "Portal"}}
        self.assertEqual(self.post("/api/v1/totals", body).json(), {"changed": 2})
        self.assertEqual(self.post("/api/v1/totals", body).json(), {"changed": 0})
        totals[0]["playtime_minutes"] = 660
        self.assertEqual(self.post("/api/v1/totals", {**body, "taken_at": T0 + 86400}).json(), {"changed": 1})
        # The Deck reports the same synced total: stored per device, but the view never adds them up.
        self.post("/api/v1/totals", {**body, "taken_at": T0 + 90000}, token=DECK_TOKEN)
        rows = self.query("SELECT account, game, playtime_minutes FROM v_lifetime_totals ORDER BY account")
        self.assertEqual(rows, [("Alice", "Portal", 660), ("Bob", "Portal", 5)])

    def test_store_names_fill_gaps_but_never_replace_manifest_names(self):
        self.post("/api/v1/totals", {"taken_at": T0, "games": {"400": "Portal"}, "totals": [
            {"account_id": 111, "app_id": 400, "playtime_minutes": 1},
            {"account_id": 111, "app_id": 620, "playtime_minutes": 1},
            {"account_id": 111, "app_id": 999, "playtime_minutes": 1}]})
        store = {400: "Wrong", 620: "Portal 2", 999: None}
        names.fill_missing_names(self.pool, fetch=store.get, pause=0)
        rows = self.query("SELECT app_id, name, source FROM games ORDER BY app_id")
        self.assertEqual(rows, [(400, "Portal", "manifest"), (620, "Portal 2", "store"), (999, None, "store")])
        self.assertEqual(names.fill_missing_names(self.pool, fetch=store.get, pause=0), 0)  # 999 waits 30 days

    def test_grafana_role_can_read(self):
        self.post("/api/v1/sync", {"sent_at": T0, "sessions": [self.session()]})
        info = psycopg.conninfo.conninfo_to_dict(DB_URL)
        info.update(user="grafana", password="grafana-test-password")
        with psycopg.connect(**info) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM v_sessions").fetchone(), (1,))
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                conn.execute("DELETE FROM sessions")

    def test_untracked_playtime_shows_as_other_device(self):
        def totals(minutes, taken_at, token=PC_TOKEN):
            self.post("/api/v1/totals", {"taken_at": taken_at, "games": {}, "totals": [
                {"account_id": 111, "app_id": 400, "playtime_minutes": minutes}]}, token=token)

        totals(1000, T0)
        self.post("/api/v1/sync", {"sent_at": T0, "sessions": [self.session(start=T0 + 3600, minutes=60)]})
        totals(1360, T0 + 86400)
        with self.pool.connection() as conn:
            self.assertEqual(catchup.recompute(conn), 1)
        rows = self.query("SELECT device, account, game, seconds, tracked FROM v_playtime ORDER BY tracked")
        self.assertEqual(rows, [("other", "Alice", "App 400", (300 - 2 - 5) * 60, False),
                                ("pc", "Alice", "App 400", 3600, True)])

    def test_sessions_split_into_local_hours(self):
        # 23:40 to 01:10 local time (the test server's TZ, UTC-6) on a recent date.
        day = datetime.fromtimestamp(T0, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        start = int((day + timedelta(hours=23 + 6, minutes=40)).timestamp())
        session = self.session(start=start, minutes=90)
        self.post("/api/v1/sync", {"sent_at": T0, "sessions": [session]})
        rows = self.query("SELECT to_char(local_hour, 'HH24:MI'), seconds, hour = local_hour AT TIME ZONE "
                          "'Etc/GMT+6' FROM v_session_hours ORDER BY hour")
        self.assertEqual(rows, [("23:00", 20 * 60, True), ("00:00", 60 * 60, True), ("01:00", 10 * 60, True)])

    def test_healthz(self):
        self.assertEqual(self.client.get("/healthz").json(), {"ok": True})


if __name__ == "__main__":
    unittest.main()
