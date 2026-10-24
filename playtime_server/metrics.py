"""Prometheus metrics, read from Postgres at scrape time: what's being played now and whether devices check in.

Live state only - the history lives in Postgres, since Prometheus keeps 90 days.
"""

from __future__ import annotations

from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily
from prometheus_client.registry import Collector

# A device that reported within this long is "online"; its current game counts as being played.
ONLINE_SECONDS = 180


class PlaytimeCollector(Collector):
    def __init__(self, pool):
        self.pool = pool

    def collect(self):
        now_playing = GaugeMetricFamily(
            "steam_playtime_now_playing", "1 while this account is playing this game on this device",
            labels=["device", "account", "app_id", "game"])
        last_seen = GaugeMetricFamily(
            "steam_playtime_device_last_seen_timestamp_seconds", "When the device last reached the server",
            labels=["device"])
        played = CounterMetricFamily(
            "steam_playtime_tracked_seconds", "Playtime in uploaded sessions", labels=["device", "account"])
        sessions = CounterMetricFamily(
            "steam_playtime_sessions", "Uploaded sessions", labels=["device", "account"])

        with self.pool.connection() as conn:
            for device, seen, account, app_id, game, online in conn.execute(
                    """
                    SELECT d.name, EXTRACT(EPOCH FROM d.last_seen_at),
                           COALESCE(a.display_name, d.current_account_id::text), d.current_app_id,
                           COALESCE(g.name, 'App ' || d.current_app_id),
                           d.last_seen_at > now() - make_interval(secs => %s)
                    FROM devices d
                    LEFT JOIN accounts a ON a.account_id = d.current_account_id
                    LEFT JOIN games g ON g.app_id = d.current_app_id
                    """, (ONLINE_SECONDS,)):
                if seen is not None:
                    last_seen.add_metric([device], float(seen))
                if online and app_id:
                    now_playing.add_metric([device, account, str(app_id), game], 1)
            for device, account, seconds, count in conn.execute(
                    "SELECT device, account, SUM(seconds), COUNT(*) FROM v_sessions GROUP BY device, account"):
                played.add_metric([device, account], float(seconds))
                sessions.add_metric([device, account], float(count))
        yield from (now_playing, last_seen, played, sessions)
