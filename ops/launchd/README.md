# ATRIUM launchd Operations

Use these files when ATRIUM should start automatically at macOS login as the
current local user. Keep secrets only in your copied local plist or `system/.env`;
do not add secrets to checked-in examples.

## Install System Service

From the repo root:

```bash
mkdir -p system/logs
cp ops/launchd/com.atrium.system.plist.example ~/Library/LaunchAgents/com.atrium.system.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.atrium.system.plist
launchctl enable gui/$(id -u)/com.atrium.system
launchctl kickstart -k gui/$(id -u)/com.atrium.system
```

Wrapper equivalent:

```bash
ops/launchd/atrium-launchd.sh install
ops/launchd/atrium-launchd.sh status
```

## Watchdog

Install the watchdog if you want macOS to restart ATRIUM when `/api/runtime`
or `/health` stops responding repeatedly:

```bash
cp ops/launchd/com.atrium.watchdog.plist.example ~/Library/LaunchAgents/com.atrium.watchdog.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.atrium.watchdog.plist
launchctl enable gui/$(id -u)/com.atrium.watchdog
launchctl kickstart -k gui/$(id -u)/com.atrium.watchdog
```

## Daily Backup

After Postgres is running:

```bash
cp ops/launchd/com.atrium.backup.plist.example ~/Library/LaunchAgents/com.atrium.backup.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.atrium.backup.plist
launchctl enable gui/$(id -u)/com.atrium.backup
launchctl kickstart -k gui/$(id -u)/com.atrium.backup
```

Useful checks:

```bash
launchctl print gui/$(id -u)/com.atrium.system
launchctl print gui/$(id -u)/com.atrium.watchdog
launchctl print gui/$(id -u)/com.atrium.backup
curl http://127.0.0.1:8787/health
```

## Stop

```bash
ops/launchd/atrium-launchd.sh uninstall
```

Or directly:

```bash
launchctl bootout gui/$(id -u)/com.atrium.system
```
