"""Where each OS keeps Steam's "who is signed in, what is running" state.

Windows: the registry (HKCU\\Software\\Valve\\Steam). SteamOS / Linux: the same keys in ~/.steam/registry.vdf. Steam
doesn't always rewrite that file promptly, so on Linux the running game also comes from the game's process, which
Steam starts through a launcher whose command line contains "SteamLaunch AppId=<id>".
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import vdf
from .steam import STEAMID64_BASE, _int


class WindowsSteam:
    KEY = r"Software\Valve\Steam"

    def __init__(self):
        import winreg
        self.winreg = winreg

    def _value(self, subkey: str, name: str):
        try:
            with self.winreg.OpenKey(self.winreg.HKEY_CURRENT_USER, subkey) as key:
                return self.winreg.QueryValueEx(key, name)[0]
        except OSError:
            return None

    def steam_root(self) -> Path | None:
        path = self._value(self.KEY, "SteamPath")
        return Path(path) if path else None

    def running(self) -> tuple[int, int]:
        """(account_id, app_id) of the signed-in account and the running game; 0 for none."""
        return (_int(self._value(self.KEY + r"\ActiveProcess", "ActiveUser")),
                _int(self._value(self.KEY, "RunningAppID")))


class LinuxSteam:
    def __init__(self, home: Path | None = None, proc: Path = Path("/proc")):
        self.home = home or Path.home()
        self.proc = proc

    def steam_root(self) -> Path | None:
        for candidate in (self.home / ".local/share/Steam", self.home / ".steam/steam"):
            if (candidate / "userdata").is_dir():
                return candidate
        return None

    def _registry(self) -> dict:
        try:
            return vdf.get_path(vdf.load(self.home / ".steam/registry.vdf"),
                                "Registry", "HKCU", "Software", "Valve", "Steam") or {}
        except (OSError, vdf.VdfError):
            return {}

    def _account_from_loginusers(self) -> int:
        root = self.steam_root()
        try:
            users = vdf.get_path(vdf.load(root / "config/loginusers.vdf"), "users") if root else None
        except (OSError, vdf.VdfError):
            return 0
        for steam_id, fields in (users or {}).items():
            if vdf.get_path(fields, "MostRecent") == "1" and _int(steam_id) > STEAMID64_BASE:
                return _int(steam_id) - STEAMID64_BASE
        return 0

    def running_app_from_processes(self) -> int:
        try:
            entries = list(self.proc.iterdir())
        except OSError:
            return 0
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                args = (entry / "cmdline").read_bytes().split(b"\0")
            except OSError:
                continue  # exited, or not ours
            if b"SteamLaunch" not in args:
                continue
            for arg in args:
                if arg.startswith(b"AppId="):
                    app_id = _int(arg[6:].decode(errors="replace"))
                    if app_id > 0:
                        return app_id
        return 0

    def running(self) -> tuple[int, int]:
        reg = self._registry()
        account_id = _int(vdf.get_path(reg, "ActiveProcess", "ActiveUser"))
        app_id = self.running_app_from_processes() or _int(vdf.get_path(reg, "RunningAppID"))
        if app_id and not account_id:
            account_id = self._account_from_loginusers()
        return account_id, app_id


def current() -> WindowsSteam | LinuxSteam:
    return WindowsSteam() if sys.platform == "win32" else LinuxSteam()


def default_dirs() -> tuple[Path, Path]:
    """(config folder, data folder) for this OS."""
    if sys.platform == "win32":
        return (Path(os.environ["APPDATA"]) / "SteamPlaytime", Path(os.environ["LOCALAPPDATA"]) / "SteamPlaytime")
    home = Path.home()
    return (Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "steam-playtime",
            Path(os.environ.get("XDG_DATA_HOME", home / ".local/share")) / "steam-playtime")
