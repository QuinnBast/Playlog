import unittest
from datetime import datetime, timedelta, timezone

try:
    from playtime_server.catchup import untracked
    HAVE_SERVER_DEPS = True
except ImportError:
    HAVE_SERVER_DEPS = False

START = datetime(2026, 11, 1, tzinfo=timezone.utc)


def day(n, hours=0):
    return START + timedelta(days=n, hours=hours)


def played(start, minutes):
    return (start, start + timedelta(minutes=minutes))


@unittest.skipUnless(HAVE_SERVER_DEPS, "server dependencies not installed")
class UntrackedTest(unittest.TestCase):
    def test_tracked_play_explains_the_growth(self):
        snaps = [(day(0), 1000, True), (day(1), 1121, False), (day(2), 1180, False)]
        sessions = [played(day(0, 12), 60), played(day(0, 18), 60), played(day(1, 12), 58)]
        self.assertEqual(untracked(snaps, sessions, day(0)), [])

    def test_untracked_play_is_reported_in_its_period(self):
        snaps = [(day(0), 1000, True), (day(1), 1060, False), (day(2), 1400, False)]
        sessions = [played(day(0, 12), 60)]
        self.assertEqual(untracked(snaps, sessions, day(0)), [(day(1), day(2), 340 - 2 - 5)])

    def test_growth_only_counted_once(self):
        snaps = [(day(0), 1000, True), (day(1), 1300, False), (day(2), 1300, False), (day(3), 1400, False)]
        rows = untracked(snaps, [], day(0))
        self.assertEqual(rows, [(day(0), day(1), 295), (day(2), day(3), 100)])

    def test_late_sessions_from_the_deck_explain_earlier_growth(self):
        # The Deck was offline: its sessions (days 1-3) reach the server on day 10, after the PC's day-5 total grew.
        snaps = [(day(0), 1000, True), (day(5), 1600, False)]
        deck = [played(day(1), 200), played(day(2), 200), played(day(3), 200)]
        self.assertEqual(untracked(snaps, [], day(0)), [(day(0), day(5), 595)])
        self.assertEqual(untracked(snaps, deck, day(0)), [])

    def test_a_devices_first_report_is_history_not_untracked_play(self):
        # PC knows nothing of this game; the Deck's first report (day 30) shows 900 minutes from before tracking.
        snaps = [(day(30), 900, True), (day(31), 960, False)]
        self.assertEqual(untracked(snaps, [played(day(30, 5), 60)], day(0)), [])

    def test_game_new_after_tracking_started_counts_from_zero(self):
        snaps = [(day(4), 300, False)]
        self.assertEqual(untracked(snaps, [played(day(3), 120)], day(0)), [(day(0), day(4), 300 - 120 - 2 - 5)])

    def test_two_devices_reporting_the_same_synced_total_count_once(self):
        snaps = [(day(0), 1000, True), (day(0), 1000, True), (day(1), 1200, False), (day(1, 1), 1200, False)]
        self.assertEqual(untracked(snaps, [played(day(0, 12), 200)], day(0)), [])


if __name__ == "__main__":
    unittest.main()
