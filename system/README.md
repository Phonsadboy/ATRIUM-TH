# ATRIUM Backend

## ภาษาไทย

โฟลเดอร์นี้คือ FastAPI backend, runtime code, migrations และ tests ของ ATRIUM ถ้าต้องการติดตั้งทั้งระบบให้เริ่มจาก [README หลัก](../README.md)

### วิธีที่แนะนำ

จาก repo root:

```bash
./atrium bootstrap --full
./atrium start
```

บน Windows native:

```powershell
.\atrium.ps1 bootstrap --full
.\atrium.ps1 start
```

### รัน backend เอง

ใช้วิธีนี้เมื่อกำลัง debug backend โดยตรง:

```bash
cp system/.env.example system/.env
docker compose up -d postgres ollama
cd system
uv sync --extra live --extra postgres --extra graph
uv run --extra postgres alembic -c alembic.ini upgrade head
uv run --extra live --extra postgres --extra graph python -m app
```

Backend เปิดที่:

```text
http://127.0.0.1:8787
```

ตรวจสถานะจริง:

```bash
curl http://127.0.0.1:8787/health
curl http://127.0.0.1:8787/api/runtime
curl http://127.0.0.1:8787/api/provider-auth/status
```

### ข้อมูลและ secrets

- เก็บ local secrets ใน `system/.env`
- ห้าม commit `system/.env`
- Runtime data อยู่ใน `system/data/`
- Logs อยู่ใน `system/logs/`
- Paths เหล่านี้ถูก ignore ไว้แล้ว

### Database

```bash
cd system
uv run --extra postgres alembic -c alembic.ini upgrade head
```

ใช้ `ops/scripts/backup_postgres.sh` สำหรับ local Postgres backups

### Tests

```bash
cd system
uv run python -m unittest discover -s tests
```

## English

This folder contains the FastAPI backend, runtime code, migrations, and tests. For full-stack installation, start from the [root README](../README.md).

### Recommended Path

From the repo root:

```bash
./atrium bootstrap --full
./atrium start
```

On Windows native:

```powershell
.\atrium.ps1 bootstrap --full
.\atrium.ps1 start
```

### Manual Backend Run

Use this when debugging the backend directly:

```bash
cp system/.env.example system/.env
docker compose up -d postgres ollama
cd system
uv sync --extra live --extra postgres --extra graph
uv run --extra postgres alembic -c alembic.ini upgrade head
uv run --extra live --extra postgres --extra graph python -m app
```

The backend runs at:

```text
http://127.0.0.1:8787
```

Runtime checks:

```bash
curl http://127.0.0.1:8787/health
curl http://127.0.0.1:8787/api/runtime
curl http://127.0.0.1:8787/api/provider-auth/status
```

### Data And Secrets

- Keep local secrets in `system/.env`
- Do not commit `system/.env`
- Runtime data belongs in `system/data/`
- Logs belong in `system/logs/`
- These paths are ignored by `.gitignore`

### Database

```bash
cd system
uv run --extra postgres alembic -c alembic.ini upgrade head
```

Use `ops/scripts/backup_postgres.sh` for local Postgres backups.

### Tests

```bash
cd system
uv run python -m unittest discover -s tests
```
