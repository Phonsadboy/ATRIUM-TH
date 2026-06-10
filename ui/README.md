# ATRIUM UI

## ภาษาไทย

โฟลเดอร์นี้คือ React + TypeScript + Vite frontend ของ ATRIUM UI จะคุยกับ backend จริงผ่าน REST และ WebSocket เท่านั้น ค่าเริ่มต้นคือ backend local ที่ `http://127.0.0.1:8787`

ถ้าต้องการรันทั้งระบบ ให้เริ่มจาก [README หลัก](../README.md)

### วิธีที่แนะนำ

จาก repo root:

```bash
./atrium setup
./atrium start
```

บน Windows native:

```powershell
.\atrium.ps1 setup
.\atrium.ps1 start
```

เปิด UI:

```text
http://127.0.0.1:5173
```

### รัน UI เอง

ใช้เมื่อ backend เปิดอยู่แล้ว:

```bash
cd ui
pnpm install
VITE_ATRIUM_API_URL="http://127.0.0.1:8787" pnpm dev --host 127.0.0.1 --port 5173
```

ถ้าใช้ PowerShell:

```powershell
cd ui
pnpm install
$env:VITE_ATRIUM_API_URL="http://127.0.0.1:8787"
pnpm dev --host 127.0.0.1 --port 5173
```

### Commands

```bash
pnpm lint
pnpm build
pnpm preview
pnpm contract:sync
```

รัน `pnpm contract:sync` หลังเปลี่ยน FastAPI routes หรือ Pydantic models เพื่ออัปเดต OpenAPI contract types ใน frontend

### Provider UI Notes

ATRIUM แสดง provider, model และ thinking effort ต่อแผนก ค่า provider/model จริงควรเช็กจาก backend ผ่าน `status --json`, `/api/runtime` และ `/api/provider-auth/status` ไม่ใช่เดาจาก UI อย่างเดียว

## English

This folder contains the React + TypeScript + Vite frontend. The UI talks to the real ATRIUM backend through REST and WebSocket. The default local backend is `http://127.0.0.1:8787`.

For full-stack setup, start from the [root README](../README.md).

### Recommended Path

From the repo root:

```bash
./atrium setup
./atrium start
```

On Windows native:

```powershell
.\atrium.ps1 setup
.\atrium.ps1 start
```

Open:

```text
http://127.0.0.1:5173
```

### Manual UI Run

Use this when the backend is already running:

```bash
cd ui
pnpm install
VITE_ATRIUM_API_URL="http://127.0.0.1:8787" pnpm dev --host 127.0.0.1 --port 5173
```

PowerShell:

```powershell
cd ui
pnpm install
$env:VITE_ATRIUM_API_URL="http://127.0.0.1:8787"
pnpm dev --host 127.0.0.1 --port 5173
```

### Commands

```bash
pnpm lint
pnpm build
pnpm preview
pnpm contract:sync
```

Run `pnpm contract:sync` after changing FastAPI routes or Pydantic models so frontend OpenAPI types stay current.

### Provider UI Notes

ATRIUM tracks provider, model, and thinking effort per department. Treat backend status as the source of truth: use `status --json`, `/api/runtime`, and `/api/provider-auth/status` before making runtime claims from the UI alone.
