import tempfile
import unittest
from pathlib import Path

from playtime_agent import steam
from playtime_agent.platforms import LinuxSteam

FIXTURES = Path(__file__).parent / "fixtures"


class SteamFilesTest(unittest.TestCase):
    def test_lifetime_totals_for_every_account(self):
        totals = sorted(steam.lifetime_totals(FIXTURES / "steam"), key=lambda t: (t["account_id"], t["app_id"]))
        self.assertEqual(totals, [
            {"account_id": 111, "app_id": 400, "playtime_minutes": 638, "last_played": 1682304246},
            {"account_id": 111, "app_id": 252950, "playtime_minutes": 159180, "last_played": 1792000000},
            {"account_id": 222, "app_id": 400, "playtime_minutes": 12, "last_played": None},
        ])

    def test_installed_game_names(self):
        self.assertEqual(steam.installed_game_names(FIXTURES / "steam"), {400: "Portal"})

    def test_missing_steam_folder(self):
        self.assertEqual(steam.lifetime_totals(FIXTURES / "nothing-here"), [])
        self.assertEqual(steam.installed_game_names(FIXTURES / "nothing-here"), {})


class LinuxSteamTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.proc = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def add_process(self, pid, *args):
        (self.proc / str(pid)).mkdir()
        (self.proc / str(pid) / "cmdline").write_bytes(b"\0".join(a.encode() for a in args) + b"\0")

    def test_registry_vdf_when_no_game_process(self):
        self.add_process(1, "/usr/lib/systemd/systemd")
        source = LinuxSteam(home=FIXTURES / "home", proc=self.proc)
        self.assertEqual(source.running(), (222, 1145360))

    def test_game_process_wins_over_registry(self):
        self.add_process(1, "/usr/lib/systemd/systemd")
        self.add_process(4242, "/home/deck/.local/share/Steam/ubuntu12_32/reaper", "SteamLaunch", "AppId=1086940",
                         "--", "/path/to/game")
        source = LinuxSteam(home=FIXTURES / "home", proc=self.proc)
        self.assertEqual(source.running(), (222, 1086940))

    def test_nothing_readable(self):
        source = LinuxSteam(home=FIXTURES / "nothing-here", proc=self.proc / "missing")
        self.assertEqual(source.running(), (0, 0))


if __name__ == "__main__":
    unittest.main()
