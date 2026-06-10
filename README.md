# ATRIUM on Mac / Windows

ATRIUM is a Thai-first, local-first workspace for running a personal AI company on your own Mac or Windows machine. It includes a FastAPI backend, Vite frontend, Postgres/pgvector, Ollama, provider auth, memory, task workflows, runtime checks, and shortcut CLIs.

Windows uses native PowerShell through `.\atrium.ps1` as the primary runtime path. WSL is only a fallback if native Windows is blocked.

Repository:

```text
https://github.com/Phonsadboy/ATRIUM-TH.git
```

## ภาษาไทย

### ATRIUM คืออะไร

ATRIUM คือ workspace สำหรับสร้าง "บริษัท AI ส่วนตัว" บนเครื่องของคุณ ผู้ใช้คุยกับ Executive แล้วระบบช่วยแตกงานไปยังแผนกต่าง ๆ เช่น research, engineering, content, operations หรือทีมที่สร้างเอง แต่ละแผนกมีบทบาท เครื่องมือ หน่วยความจำ งานที่ได้รับมอบหมาย artifacts และสถานะ runtime ที่ตรวจสอบได้

เป้าหมายของโปรเจ็กนี้คือให้คนทั่วไปลองใช้ multi-agent workspace ได้เร็วขึ้น ไม่ต้องประกอบ backend, frontend, database, provider auth, memory, logs, health checks และ runbook เองทั้งหมดจากศูนย์

จุดสำคัญ:

- Thai-first onboarding และ prompt flow
- Local-first บน macOS และ Windows
- UI + backend + database + runtime checks พร้อมใน repo เดียว
- รองรับ Claude Code account, ChatGPT account OAuth, OpenAI/Anthropic API key และ local/Ollama embeddings
- ใช้ `/api/runtime`, provider status, logs และ redacted report เป็นหลักฐานความพร้อมจริง

### ติดตั้งด้วย Codex/Claude ก่อน

แนะนำวิธีนี้ก่อน เพราะ agent จะช่วย clone repo, ติดตั้ง dependency, start stack, ตรวจ runtime และบอกให้ผู้ใช้กดอนุญาตเฉพาะจุดที่ระบบปฏิบัติการต้องการ

ก่อนเริ่ม:

- เปิด Codex หรือ Claude Code บนเครื่องที่จะติดตั้งจริง
- ใช้ Coding / Code mode
- เปิดสิทธิ์ Full Access หรือเทียบเท่า
- ห้ามส่ง password, API key, OAuth token หรือ secret ในแชต
- ถ้า macOS/Windows ขอ password, UAC, firewall, Docker Desktop, browser permission หรือ provider login ให้ผู้ใช้กดเอง แล้วบอก agent ว่าเสร็จแล้ว

#### macOS ด้วย Codex/Claude

ส่ง prompt นี้:

```text
ติดตั้ง ATRIUM บน macOS จาก https://github.com/Phonsadboy/ATRIUM-TH.git
ใช้ path ~/Projects/ai-company
ห้ามวาง repo ใน iCloud Drive, Desktop หรือ Documents ที่ sync กับ iCloud
ถ้ายังไม่มี repo ให้ clone ก่อน แล้วรัน ./atrium setup --yes
ตรวจให้จบด้วย ./atrium status --json, ./atrium provider status --probe --json, ./atrium tools status --json, ./atrium automation status --commands และ ./atrium report --bundle
เป้าหมายคือเปิด http://127.0.0.1:5173 ให้ใช้งานได้จริง
ห้ามขอหรือพิมพ์ password, API key, OAuth token หรือ secret ในแชต
ถ้าต้องเปิด Docker Desktop, ใส่รหัสเครื่อง, login provider หรือกด macOS permission ให้บอกฉันชัดเจนแล้วรอ
```

#### Windows native ด้วย Codex/Claude

ส่ง prompt นี้:

```text
ติดตั้ง ATRIUM บน Windows native PowerShell จาก https://github.com/Phonsadboy/ATRIUM-TH.git
ใช้ .\atrium.ps1 เป็น runtime-control path หลัก
clone repo ไว้ที่ %USERPROFILE%\Projects\ai-company หรือ path local ที่ไม่อยู่ใน OneDrive, Desktop, Documents หรือ Downloads
เตรียม Docker Desktop สำหรับ Docker-backed services และ Claude Code CLI สำหรับ Claude account provider ถ้าจำเป็น
หลัง clone ให้รัน .\atrium.ps1 setup --yes
ตรวจให้จบด้วย .\atrium.ps1 status --json, .\atrium.ps1 provider status --probe --json, .\atrium.ps1 tools status --json, .\atrium.ps1 automation status --commands และ .\atrium.ps1 report --bundle
เป้าหมายคือเปิด http://127.0.0.1:5173 จาก Windows browser ให้ใช้งานได้จริง
ห้ามขอหรือพิมพ์ password, API key, OAuth token หรือ secret ในแชต
ถ้าต้อง restart Windows, เปิด Docker Desktop, กด UAC/firewall หรือ login provider ให้บอกฉันชัดเจนแล้วรอ
```

### ติดตั้งเอง

#### macOS

```bash
curl -fsSL https://raw.githubusercontent.com/Phonsadboy/ATRIUM-TH/main/ops/install_macos.sh -o /tmp/atrium-install-macos.sh
bash /tmp/atrium-install-macos.sh
```

ถ้า clone repo ไว้แล้ว:

```bash
cd ~/Projects/ai-company
./atrium setup
```

#### Windows native PowerShell

เปิด PowerShell แล้วรัน:

```powershell
$script="$env:TEMP\atrium-windows-native-install.ps1"
Invoke-WebRequest -UseBasicParsing "https://raw.githubusercontent.com/Phonsadboy/ATRIUM-TH/main/ops/install_windows_native.ps1" -OutFile $script
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script
```

ถ้า clone repo ไว้แล้ว:

```powershell
cd $env:USERPROFILE\Projects\ai-company
.\atrium.ps1 setup
```

### คำสั่งหลัก

| งาน | macOS | Windows native |
| --- | --- | --- |
| ติดตั้ง/เตรียมระบบ | `./atrium setup` | `.\atrium.ps1 setup` |
| เปิดระบบ | `./atrium start` | `.\atrium.ps1 start` |
| ตรวจ runtime | `./atrium status --json` | `.\atrium.ps1 status --json` |
| ตรวจ provider | `./atrium provider status --probe --json` | `.\atrium.ps1 provider status --probe --json` |
| ตรวจ tools | `./atrium tools status --json` | `.\atrium.ps1 tools status --json` |
| ดู log | `./atrium logs` | `.\atrium.ps1 logs` |
| สร้าง support bundle | `./atrium report --bundle` | `.\atrium.ps1 report --bundle` |
| หยุดระบบ | `./atrium stop` | `.\atrium.ps1 stop` |

เปิดใช้งานที่:

```text
http://127.0.0.1:5173
```

### เช็กว่าพร้อมใช้งานจริง

พร้อมใช้งานจริงหมายถึง:

- Frontend เปิดที่ `http://127.0.0.1:5173`
- Backend เปิดที่ `http://127.0.0.1:8787`
- `/health` ผ่าน
- `/api/runtime` แสดง runtime, provider และ embedding state ที่ตรงกับเครื่องจริง
- มี provider อย่างน้อย 1 ตัวพร้อมตอบงาน หรือ `status` บอก blocker ชัดเจน
- `report --bundle` สร้าง redacted support zip ได้โดยไม่พิมพ์ secret

ถ้าติดปัญหา ให้ส่ง agent ทำต่อด้วย:

```text
ทำต่อจากจุดที่ค้าง ตรวจ full stack, provider readiness, /api/runtime, logs และเปิด ATRIUM ให้พร้อมใช้งานจริง
```

รายละเอียดเชิง operations อยู่ที่ [ops/README.md](ops/README.md)  
รายละเอียด backend อยู่ที่ [system/README.md](system/README.md)  
รายละเอียด frontend อยู่ที่ [ui/README.md](ui/README.md)

## English

### What ATRIUM Is

ATRIUM is a local workspace for running a personal AI company on your Mac or Windows machine. You work with an Executive, then ATRIUM can route work to departments such as research, engineering, content, operations, or custom teams. Each department has roles, tools, memory, assigned work, artifacts, and visible runtime status.

The goal is to make a real multi-agent workspace easier to run: UI, backend, database, provider auth, memory, logs, health checks, and operating commands are included in one repo.

Highlights:

- Thai-first onboarding and workflows
- Local-first on macOS and Windows
- FastAPI backend, Vite frontend, Postgres/pgvector, Ollama, and runtime checks
- Claude Code account, ChatGPT account OAuth, OpenAI/Anthropic API keys, and local/Ollama embeddings
- Runtime truth through `/api/runtime`, provider status, logs, and redacted reports

### Start With Codex/Claude Assisted Install

This is the recommended path. The agent can clone the repo, install dependencies, start the stack, verify runtime state, and stop whenever the OS or provider needs a user approval.

Before starting:

- Open Codex or Claude Code on the target machine
- Use Coding / Code mode
- Enable Full Access or equivalent permissions
- Do not paste passwords, API keys, OAuth tokens, or secrets into chat
- Handle OS prompts, Docker Desktop first-run prompts, browser permissions, and provider login yourself

#### macOS With Codex/Claude

Send this prompt:

```text
Install ATRIUM on macOS from https://github.com/Phonsadboy/ATRIUM-TH.git.
Use ~/Projects/ai-company.
Do not place the repo in iCloud Drive, Desktop, or Documents if those folders sync with iCloud.
Clone the repo if needed, then run ./atrium setup --yes.
Finish by running ./atrium status --json, ./atrium provider status --probe --json, ./atrium tools status --json, ./atrium automation status --commands, and ./atrium report --bundle.
The goal is to open http://127.0.0.1:5173 and make the full stack usable.
Never ask for or print passwords, API keys, OAuth tokens, or secrets in chat.
If Docker Desktop, machine password, provider login, or macOS permission approval is needed, tell me exactly what to do and wait.
```

#### Windows Native With Codex/Claude

Send this prompt:

```text
Install ATRIUM on Windows native PowerShell from https://github.com/Phonsadboy/ATRIUM-TH.git.
Use .\atrium.ps1 as the main runtime-control path.
Clone the repo into %USERPROFILE%\Projects\ai-company or another local path outside OneDrive, Desktop, Documents, and Downloads.
Prepare Docker Desktop for Docker-backed services and Claude Code CLI for the Claude account provider if needed.
After cloning, run .\atrium.ps1 setup --yes.
Finish by running .\atrium.ps1 status --json, .\atrium.ps1 provider status --probe --json, .\atrium.ps1 tools status --json, .\atrium.ps1 automation status --commands, and .\atrium.ps1 report --bundle.
The goal is to open http://127.0.0.1:5173 from a Windows browser and make the full stack usable.
Never ask for or print passwords, API keys, OAuth tokens, or secrets in chat.
If Windows restart, Docker Desktop, UAC/firewall approval, or provider login is needed, tell me exactly what to do and wait.
```

### Manual Install

#### macOS

```bash
curl -fsSL https://raw.githubusercontent.com/Phonsadboy/ATRIUM-TH/main/ops/install_macos.sh -o /tmp/atrium-install-macos.sh
bash /tmp/atrium-install-macos.sh
```

If the repo already exists:

```bash
cd ~/Projects/ai-company
./atrium setup
```

#### Windows Native PowerShell

```powershell
$script="$env:TEMP\atrium-windows-native-install.ps1"
Invoke-WebRequest -UseBasicParsing "https://raw.githubusercontent.com/Phonsadboy/ATRIUM-TH/main/ops/install_windows_native.ps1" -OutFile $script
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script
```

If the repo already exists:

```powershell
cd $env:USERPROFILE\Projects\ai-company
.\atrium.ps1 setup
```

### Core Commands

| Task | macOS | Windows native |
| --- | --- | --- |
| Setup | `./atrium setup` | `.\atrium.ps1 setup` |
| Start | `./atrium start` | `.\atrium.ps1 start` |
| Runtime status | `./atrium status --json` | `.\atrium.ps1 status --json` |
| Provider status | `./atrium provider status --probe --json` | `.\atrium.ps1 provider status --probe --json` |
| Tool status | `./atrium tools status --json` | `.\atrium.ps1 tools status --json` |
| Logs | `./atrium logs` | `.\atrium.ps1 logs` |
| Support bundle | `./atrium report --bundle` | `.\atrium.ps1 report --bundle` |
| Stop | `./atrium stop` | `.\atrium.ps1 stop` |

Open:

```text
http://127.0.0.1:5173
```

### Ready Means

- Frontend is reachable at `http://127.0.0.1:5173`
- Backend is reachable at `http://127.0.0.1:8787`
- `/health` passes
- `/api/runtime` shows current runtime, provider, and embedding state
- At least one provider is ready, or `status` explains the blocker
- `report --bundle` can produce a redacted support zip without printing secrets

For deeper operations, see [ops/README.md](ops/README.md).  
For backend work, see [system/README.md](system/README.md).  
For frontend work, see [ui/README.md](ui/README.md).
