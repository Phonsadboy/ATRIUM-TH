#!/usr/bin/env bash
set -euo pipefail

LABEL="com.atrium.system"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC_PLIST="$ROOT/ops/launchd/$LABEL.plist.example"
DST_PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"
SERVICE="$DOMAIN/$LABEL"
LOG_DIR="$ROOT/system/logs"

usage() {
  cat <<EOF
Usage: $0 <install|uninstall|restart|status> [--force] [--remove-plist]

install        Copy the example plist if needed, bootstrap, enable, and start.
uninstall      Stop the LaunchAgent. Add --remove-plist to delete the copied plist.
restart        Kickstart the installed LaunchAgent.
status         Print launchctl status and localhost /health if reachable.

Options:
  --force       Overwrite the copied plist during install.
  --remove-plist
                Delete ~/Library/LaunchAgents/$LABEL.plist during uninstall.
EOF
}

loaded() {
  launchctl print "$SERVICE" >/dev/null 2>&1
}

cmd="${1:-}"
shift || true
force=0
remove_plist=0
for arg in "$@"; do
  case "$arg" in
    --force) force=1 ;;
    --remove-plist) remove_plist=1 ;;
    *) echo "unknown option: $arg" >&2; usage; exit 2 ;;
  esac
done

case "$cmd" in
  install)
    mkdir -p "$LOG_DIR" "$HOME/Library/LaunchAgents"
    if [[ -e "$DST_PLIST" && "$force" -ne 1 ]]; then
      echo "plist already exists: $DST_PLIST"
      echo "use --force to overwrite it after reviewing local secrets/config"
    else
      cp "$SRC_PLIST" "$DST_PLIST"
      echo "copied $DST_PLIST"
    fi
    if loaded; then
      echo "$LABEL is already loaded"
    else
      launchctl bootstrap "$DOMAIN" "$DST_PLIST"
    fi
    launchctl enable "$SERVICE"
    launchctl kickstart -k "$SERVICE"
    "$ROOT/ops/launchd/atrium-launchd.sh" status
    ;;
  uninstall)
    if loaded; then
      launchctl bootout "$SERVICE"
      echo "stopped $LABEL"
    else
      echo "$LABEL is not loaded"
    fi
    if [[ "$remove_plist" -eq 1 && -e "$DST_PLIST" ]]; then
      rm "$DST_PLIST"
      echo "removed $DST_PLIST"
    fi
    ;;
  restart)
    launchctl kickstart -k "$SERVICE"
    ;;
  status)
    if loaded; then
      launchctl print "$SERVICE" | sed -n '1,80p'
    else
      echo "$LABEL is not loaded"
    fi
    if command -v curl >/dev/null 2>&1; then
      curl -fsS --max-time 2 http://127.0.0.1:8787/health || true
      echo
    fi
    ;;
  ""|-h|--help|help)
    usage
    ;;
  *)
    echo "unknown command: $cmd" >&2
    usage
    exit 2
    ;;
esac
