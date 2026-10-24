import unittest
from pathlib import Path

from playtime_agent.agent import Agent, BACKOFF_MIN, TOTALS_EVERY_SECONDS
from playtime_agent.store import Store
from playtime_agent.uploader import UploadError

FIXTURES = Path(__file__).parent / "fixtures"
T0 = 1_800_000_000


class FakeSteam:
    def __init__(self):
        self.state = (0, 0)

    def steam_root(self):
        return FIXTURES / "steam"

    def running(self):
        return self.state


class FakeServer:
    def __init__(self):
        self.up = True
        self.sessions = {}
        self.current = None
        self.totals_calls = []

    def sync(self, sessions, current, now):
        if not self.up:
            raise UploadError("down")
        for s in sessions:
            self.sessions[s["uuid"]] = s
        self.current = current
        return [s["uuid"] for s in sessions]

    def totals(self, totals, names, now):
        if not self.up:
            raise UploadError("down")
        self.totals_calls.append((now, totals, names))


class AgentTest(unittest.TestCase):
    def setUp(self):
        self.now = T0
        self.steam = FakeSteam()
        self.server = FakeServer()
        self.store = Store(":memory:")
        self.agent = self.make_agent()

    def make_agent(self):
        return Agent(self.steam, self.store, self.server, clock=lambda: self.now)

    def run_minutes(self, minutes, state):
        self.steam.state = state
        for _ in range(minutes):
            self.agent.tick()
            self.now += 60

    def test_session_uploaded_with_live_status(self):
        self.run_minutes(3, (111, 400))
        self.assertEqual(self.server.current["app_id"], 400)
        self.run_minutes(1, (111, 0))
        self.assertIsNone(self.server.current)
        [session] = self.server.sessions.values()
        self.assertEqual((session["started_at"], session["ended_at"]), (T0, T0 + 120))
        self.assertEqual(self.store.pending(), [])

    def test_queues_while_offline_and_backs_off(self):
        self.server.up = False
        self.run_minutes(5, (111, 400))
        self.run_minutes(2, (111, 0))
        self.assertEqual(len(self.store.pending()), 1)
        self.assertGreaterEqual(self.agent.next_contact - self.now, 0)
        self.server.up = True
        self.now = self.agent.next_contact
        self.agent.tick()
        self.assertEqual(len(self.server.sessions), 1)
        self.assertEqual(self.store.pending(), [])

    def test_backoff_grows_then_resets(self):
        self.server.up = False
        self.agent.tick()
        self.assertEqual(self.agent.backoff, BACKOFF_MIN)
        self.now = self.agent.next_contact
        self.agent.tick()
        self.assertEqual(self.agent.backoff, 2 * BACKOFF_MIN)
        self.server.up = True
        self.now = self.agent.next_contact
        self.agent.tick()
        self.assertEqual(self.agent.backoff, 0)

    def test_open_session_survives_a_restart(self):
        self.run_minutes(2, (111, 400))
        uuid = self.agent.tracker.current.uuid
        self.agent = self.make_agent()
        self.run_minutes(1, (111, 400))
        self.assertEqual(self.agent.tracker.current.uuid, uuid)
        self.assertEqual(self.agent.tracker.current.started_at, T0)

    def test_totals_daily_and_only_after_sessions_are_in(self):
        self.run_minutes(1, (111, 400))
        self.assertEqual(self.server.totals_calls, [])  # a game is running
        self.run_minutes(1, (111, 0))
        self.assertEqual(len(self.server.totals_calls), 1)
        _, totals, names = self.server.totals_calls[0]
        self.assertEqual(len(totals), 3)
        self.assertEqual(names, {400: "Portal"})
        self.run_minutes(10, (0, 0))
        self.assertEqual(len(self.server.totals_calls), 1)
        self.now += TOTALS_EVERY_SECONDS
        self.agent.tick()
        self.assertEqual(len(self.server.totals_calls), 2)

    def test_non_steam_shortcuts_ignored(self):
        self.run_minutes(3, (111, 3_000_000_000))
        self.assertIsNone(self.agent.tracker.current)


if __name__ == "__main__":
    unittest.main()
