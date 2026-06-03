# ATRIUM Operations

This folder is intentionally minimal. It contains only the files needed for
local installation, macOS startup, account OAuth, and database maintenance.

## Included

- `chatgpt_account_oauth_login.py` - local ChatGPT account OAuth login helper
- `windows_host_bridge_probe.py` - Windows HostBridge parity probe for shell,
  browser, desktop, notification, and interactive desktop checks
- `launchd/` - macOS LaunchAgent examples and wrapper scripts
- `scripts/backup_postgres.sh` - local Postgres backup script
- `scripts/migrate_sqlite_to_postgres.py` - one-time SQLite to Postgres migration helper

## Full Local Stack

From the repo root:

```bash
cp system/.env.example system/.env
docker compose up -d postgres ollama
docker compose --profile v2 up -d letta
cd system
uv run --extra postgres alembic -c alembic.ini upgrade head
uv run --extra live --extra postgres --extra graph python -m app
```

In another terminal:

```bash
cd ui
pnpm install
pnpm dev --host 127.0.0.1 --port 5173
```

## macOS launchd

See `ops/launchd/README.md` when ATRIUM should start automatically at login.

## Windows HostBridge

Use the simulation mode from macOS/Linux for branch coverage:

```bash
uv --project system run python ops/windows_host_bridge_probe.py --simulate
```

Run the full parity probe only from a signed-in Windows desktop session:

```powershell
uv --project system run python ops/windows_host_bridge_probe.py --full
```

## Backups

The backup script writes local dumps under `system/data/backups` by default:

```bash
ops/scripts/backup_postgres.sh
```

Do not commit `system/.env`, logs, database files, or backup dumps.
