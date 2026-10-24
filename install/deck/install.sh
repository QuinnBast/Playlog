#!/bin/bash
# Installs the playtime agent on a Steam Deck (or any Linux with Steam) for the current user. Everything lives in the
# home folder, so SteamOS updates don't remove it; nothing needs sudo or a package install (SteamOS has python3).
#
#   ./install.sh --server https://playlog.example.lan --token <deck token> [--device deck]
#   ./install.sh --uninstall
set -euo pipefail

server=""
device="deck"
token=""
uninstall=false
while [ $# -gt 0 ]; do
    case "$1" in
        --server) server="$2"; shift 2 ;;
        --device) device="$2"; shift 2 ;;
        --token) token="$2"; shift 2 ;;
        --uninstall) uninstall=true; shift ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

here="$(cd "$(dirname "$0")" && pwd)"
app_dir="$HOME/.local/share/steam-playtime/app"
config="$HOME/.config/steam-playtime/config.json"
unit_dir="$HOME/.config/systemd/user"

systemctl --user disable --now steam-playtime.service 2>/dev/null || true
if $uninstall; then
    rm -rf "$app_dir" "$unit_dir/steam-playtime.service"
    systemctl --user daemon-reload
    echo "Removed the service and $app_dir. Kept $config and the queue in ~/.local/share/steam-playtime."
    exit 0
fi

python3 -c 'import sys; assert sys.version_info >= (3, 10), sys.version' \
    || { echo "Needs Python 3.10 or newer" >&2; exit 1; }

mkdir -p "$app_dir" "$unit_dir" "$(dirname "$config")"
rm -rf "$app_dir/playtime_agent"
cp -r "$here/../../playtime_agent" "$app_dir/"
find "$app_dir" -name __pycache__ -prune -exec rm -rf {} +

if [ ! -f "$config" ]; then
    [ -n "$token" ] && [ -n "$server" ] || { echo "No config yet: pass --server <server> --token <this device's token>" >&2; exit 2; }
    umask 077
    printf '{"server_url": "%s", "device": "%s", "token": "%s", "interval_seconds": 60}\n' \
        "$server" "$device" "$token" > "$config"
    echo "Wrote $config"
fi

cp "$here/steam-playtime.service" "$unit_dir/"
systemctl --user daemon-reload
systemctl --user enable --now steam-playtime.service
echo
(cd "$app_dir" && python3 -m playtime_agent --check) || true
echo
echo "Installed and started. Status: systemctl --user status steam-playtime"
echo "Log: ~/.local/share/steam-playtime/agent.log"
