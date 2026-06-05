# ATRIUM Backend

This folder contains the FastAPI backend, runtime code, migrations, and tests.
Use the root `README.md` for the full beginner-friendly macOS/Windows installation guide.

## Setup

From the repo root:

```bash
cp system/.env.example system/.env
cd system
uv sync --extra live --extra postgres --extra graph
uv run --extra postgres alembic -c alembic.ini upgrade head
uv run --extra live --extra postgres --extra graph python -m app
```

The backend runs at:

```text
http://127.0.0.1:8787
```

Useful checks:

```bash
curl http://127.0.0.1:8787/health
curl http://127.0.0.1:8787/api/runtime
curl http://127.0.0.1:8787/api/provider-auth/status
```

## Data And Secrets

- Keep local secrets in `system/.env`
- Do not commit `system/.env`
- Runtime data belongs in `system/data/`
- Logs belong in `system/logs/`

These paths are ignored by `.gitignore`.

## Database

Run migrations with:

```bash
cd system
uv run --extra postgres alembic -c alembic.ini upgrade head
```

Use `ops/scripts/backup_postgres.sh` for local Postgres backups.

## Tests

Run focused backend tests from `system/`:

```bash
uv run python -m unittest discover -s tests
```
