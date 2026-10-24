import unittest

from playtime_agent.tracker import Tracker

T0 = 1_800_000_000


class TrackerTest(unittest.TestCase):
    def play(self, tracker, observations):
        finished = []
        for offset, account, app in observations:
            finished += tracker.observe(T0 + offset, account, app)
        return finished

    def test_one_session_until_the_game_stops(self):
        t = Tracker()
        done = self.play(t, [(0, 1, 400), (60, 1, 400), (120, 1, 400), (180, 1, 0)])
        self.assertEqual(len(done), 1)
        self.assertEqual((done[0].account_id, done[0].app_id, done[0].started_at, done[0].ended_at),
                         (1, 400, T0, T0 + 120))
        self.assertIsNone(t.current)

    def test_nothing_running_records_nothing(self):
        t = Tracker()
        self.assertEqual(self.play(t, [(0, 1, 0), (60, 0, 0), (120, 0, 400)]), [])
        self.assertIsNone(t.current)

    def test_switching_game_or_account_starts_a_new_session(self):
        t = Tracker()
        done = self.play(t, [(0, 1, 400), (60, 1, 500), (120, 2, 500)])
        self.assertEqual([(s.account_id, s.app_id) for s in done], [(1, 400), (1, 500)])
        self.assertEqual((t.current.account_id, t.current.app_id, t.current.started_at), (2, 500, T0 + 120))

    def test_sleep_splits_the_session_at_the_last_observation(self):
        t = Tracker()
        done = self.play(t, [(0, 1, 400), (60, 1, 400), (60 + 181, 1, 400), (60 + 241, 1, 400)])
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0].ended_at, T0 + 60)
        self.assertEqual((t.current.started_at, t.current.ended_at), (T0 + 241, T0 + 301))

    def test_gap_within_the_limit_continues(self):
        t = Tracker()
        self.assertEqual(self.play(t, [(0, 1, 400), (180, 1, 400)]), [])
        self.assertEqual(t.current.ended_at, T0 + 180)

    def test_clock_going_backwards_splits(self):
        t = Tracker()
        done = self.play(t, [(0, 1, 400), (60, 1, 400), (-3600, 1, 400)])
        self.assertEqual(done[0].ended_at, T0 + 60)
        self.assertEqual(t.current.started_at, T0 - 3600)

    def test_sessions_get_distinct_ids(self):
        t = Tracker()
        done = self.play(t, [(0, 1, 400), (60, 1, 500), (120, 1, 400), (180, 1, 0)])
        self.assertEqual(len({s.uuid for s in done}), 3)


if __name__ == "__main__":
    unittest.main()
