# ATRIUM on Mac / Windows

ATRIUM is a Thai-first, local-first full-stack AI company workspace for macOS and Windows. This repo includes the FastAPI backend, Vite frontend, Docker services, runtime configuration, and shortcut CLIs for setting up the local full stack. Windows uses a native PowerShell path for runtime control.

Repository:

```text
https://github.com/Phonsadboy/ATRIUM-TH.git
```

## โปรเจ็กนี้คืออะไร

ATRIUM คือ workspace สำหรับสร้าง “บริษัท AI ส่วนตัว” บนเครื่องของคุณ ไม่ใช่แค่ library สำหรับประกอบ agent เองจากศูนย์ ผู้ใช้คุยกับ Executive แล้วให้ระบบแตกงานไปยังแผนกต่าง ๆ เช่น research, engineering, content, operations หรือทีมที่คุณสร้างเอง แต่ละแผนกมีบทบาท เครื่องมือ หน่วยความจำ งานที่ได้รับมอบหมาย และสถานะการทำงานที่ตรวจสอบได้

แนวคิดของโปรเจ็กนี้มาจากปัญหาที่เจอบ่อยในโลก AI agent/multi-agent: demo ดูน่าตื่นเต้น แต่พอจะใช้จริงต้องประกอบ backend, frontend, database, runtime, provider auth, memory, tool permissions, logs, health checks และ runbook เองทั้งหมด ATRIUM เลยเลือกเป็น full-stack local app ตั้งแต่แรก เปิด browser แล้วเห็นบริษัท AI กำลังทำงานจริง ไม่ใช่แค่ agents คุยกันใน terminal

### จุดเด่นของ ATRIUM

- **Thai-first**: ออกแบบให้ผู้ใช้ไทยเริ่มได้ง่ายขึ้น ทั้ง prompt, onboarding และ flow การใช้งาน
- **Local-first บน Mac/Windows**: ข้อมูล runtime, memory, database และ workspace อยู่ในเครื่องคุณเป็นหลัก โดย Windows ใช้ native PowerShell path
- **AI company ไม่ใช่แค่ agent chat**: มี Executive, แผนก, task, approval, handoff, memory, artifacts และสถานะ runtime
- **Full stack พร้อมใช้**: FastAPI backend, Vite frontend, Postgres/pgvector, Ollama และ setup shortcut ผ่าน `./atrium` บน macOS หรือ `.\atrium.ps1` บน Windows
- **ใช้ provider ได้หลายแบบ**: Claude Code account, ChatGPT account OAuth, OpenAI Platform API key, Anthropic API key และ local embedding ผ่าน Ollama
- **เห็นความจริงของระบบ**: `./atrium doctor` / `.\atrium.ps1 doctor`, `./atrium status` / `.\atrium.ps1 status`, `/health`, `/api/runtime` และ redacted support report ช่วยแยกว่าอะไรพร้อม อะไรยังติด blocker
- **มนุษย์ยังคุมงานสำคัญ**: งานที่กระทบระบบจริงควรมี approval, credential readiness และ status ที่ตรวจสอบได้ แทนการปล่อย agent ทำทุกอย่างแบบมองไม่เห็น

### ต่างจาก framework ทั่วไปอย่างไร

CrewAI, LangGraph, AutoGen/Microsoft Agent Framework และ MetaGPT ให้บทเรียนสำคัญกับ ecosystem นี้: agent ควรมีบทบาทเฉพาะ, workflow ควรมี state, team ควรถูก orchestrate และงานซับซ้อนต้องมีโครงสร้าง แต่หลายโปรเจ็กยังเป็น framework สำหรับนักพัฒนาเป็นหลัก คุณยังต้องตัดสินใจเองว่าจะเก็บข้อมูลที่ไหน เปิด UI อย่างไร จัดการ secret อย่างไร ตรวจ health อย่างไร และอธิบายให้ผู้ใช้ใหม่ setup ยังไง

ATRIUM จึงโฟกัสอีกด้านหนึ่ง: ทำให้ multi-agent กลายเป็น workspace ที่คนทั่วไปลองใช้ได้เร็วขึ้น มี UI มี runtime truth มี setup script มีสถานะตรวจสอบได้ และออกแบบจากบริบทผู้ใช้ไทยตั้งแต่แรก โปรเจ็กนี้ยังไม่ใช่ระบบวิเศษที่ทำงานแทนคนได้ทุกเรื่อง คุณยังต้องมีบัญชี AI, เปิด Docker, ให้ permission และตรวจงานสำคัญเอง แต่เป้าหมายคือทำให้เส้นทางจาก “อยากลองบริษัท AI” ไปถึง “เปิดใช้งานจริงบนเครื่องตัวเอง” สั้นและชัดที่สุด

## ภาษาไทย: วิธีติดตั้ง

เลือกตามระบบปฏิบัติการก่อน แล้วเลือกวิธีติดตั้ง:

- **ทำเอง**: ผู้ใช้คัดลอกคำสั่งไปรันเอง เหมาะกับคนที่คุ้น Terminal/PowerShell
- **ให้ Codex/Claude ทำให้**: เปิด Codex หรือ Claude Code แล้วสั่งให้ agent ติดตั้ง ผู้ใช้รอและกดอนุญาตเฉพาะหน้าต่างที่ระบบปฏิบัติการถาม

ก่อนใช้วิธี Codex/Claude ให้ตั้งค่า agent ให้เหมาะกับงานติดตั้ง:

- ใช้ Codex หรือ Claude Code ใน **Coding / Code mode**
- เปิดสิทธิ์เป็น **Full Access** หรือเทียบเท่า ให้ agent อ่าน/แก้ไฟล์, รัน terminal command, ใช้ network และเปิด browser ได้
- เปิด agent ในเครื่องที่จะติดตั้งจริง ไม่ใช่เครื่องอื่น
- ห้ามส่ง password, API key, OAuth token หรือ secret ลงในแชต ให้ผู้ใช้กรอกเองในหน้าต่างของระบบหรือ provider
- ถ้า macOS/Windows ขอ password, restart, permission, firewall, Docker Desktop หรือ provider login ให้ผู้ใช้ทำเอง แล้วบอก agent ว่าเสร็จแล้ว

### macOS

macOS แนะนำให้ติดตั้งที่ `~/Projects/ai-company` และหลีกเลี่ยง iCloud Drive, Desktop หรือ Documents ที่ sync กับ iCloud

#### วิธีที่ 1: ทำเอง

เปิด Terminal แล้วรัน:

```bash
curl -fsSL https://raw.githubusercontent.com/Phonsadboy/ATRIUM-TH/main/ops/install_macos.sh -o /tmp/atrium-install-macos.sh && bash /tmp/atrium-install-macos.sh
```

ถ้า clone repo ไว้แล้ว:

```bash
cd ~/Projects/ai-company
./atrium setup
```

สคริปต์จะเตรียมเครื่อง, ติดตั้ง dependency ที่ขาด, bootstrap full stack, start backend/frontend, ตรวจสถานะ และเปิด browser ไปที่ `http://127.0.0.1:5173`

#### วิธีที่ 2: ให้ Codex/Claude ทำให้

เปิด Codex หรือ Claude Code ใน Coding/Code mode พร้อม Full Access แล้วส่งข้อความนี้:

```text
ติดตั้ง ATRIUM บน macOS จาก https://github.com/Phonsadboy/ATRIUM-TH.git
ใช้ path ~/Projects/ai-company
ห้ามวาง repo ใน iCloud Drive, Desktop หรือ Documents ที่ sync กับ iCloud
ให้ clone repo ถ้ายังไม่มี แล้วรัน ./atrium setup --yes
ตรวจสอบให้จบด้วย ./atrium status
เป้าหมายคือเปิด http://127.0.0.1:5173 ให้ใช้งานได้จริง
ห้ามขอหรือพิมพ์ password, API key, OAuth token หรือ secret ในแชต
ถ้าต้องเปิด Docker Desktop, ใส่รหัสเครื่อง, login provider หรือกด macOS permission ให้บอกฉันชัดเจนแล้วรอ
```

### Windows Native PowerShell

Windows native path ใช้ PowerShell เป็นตัวควบคุม backend/frontend โดยตรง และใช้ Docker Desktop สำหรับ Postgres/Ollama ในเฟสแรก
มี `atrium.cmd` เป็น shim สำหรับ Windows Terminal/cmd.exe ที่เรียก `.\atrium.ps1` ด้วย ExecutionPolicy Bypass ให้เอง แต่คำสั่งหลักในเอกสารยังใช้ PowerShell โดยตรง

ถ้ายังไม่ได้ clone repo ให้เปิด PowerShell แล้วรัน installer native:

```powershell
$script="$env:TEMP\atrium-windows-native-install.ps1"; Invoke-WebRequest -UseBasicParsing "https://raw.githubusercontent.com/Phonsadboy/ATRIUM-TH/main/ops/install_windows_native.ps1" -OutFile $script; powershell -ExecutionPolicy Bypass -File $script
```

ถ้าต้องการข้ามบางส่วนชั่วคราว ใช้ flag เช่น `-NoStart`, `-SkipDockerInstall`, `-SkipBrowserInstall`, หรือ `-SkipClaudeCodeInstall`

เปิด PowerShell ใน repo แล้วรัน:

```powershell
.\atrium.ps1 setup
```

คำสั่งหลัก:

```powershell
.\atrium.ps1 doctor
.\atrium.ps1 bootstrap --full
.\atrium.ps1 start
.\atrium.ps1 restart
.\atrium.ps1 tools status
.\atrium.ps1 tools status --json
.\atrium.ps1 tools catalog
.\atrium.ps1 tools catalog --json
.\atrium.ps1 provider status --probe
.\atrium.ps1 provider status --probe --json
.\atrium.ps1 provider login chatgpt
.\atrium.ps1 provider login claude-code
.\atrium.ps1 provider disconnect chatgpt
.\atrium.ps1 automation status --commands
.\atrium.ps1 automation status --json
.\atrium.ps1 automation source
.\atrium.ps1 automation audit
.\atrium.ps1 automation handoff --macos <macos-json>
.\atrium.ps1 automation windows-live-proof --parity-run-id <run-id> --source-fingerprint <fingerprint> --source-manifest-sha256 <manifest> --source-file-count <count>
.\atrium.ps1 automation artifact --label windows --expect-parity-run-id <run-id> <windows-json>
.\atrium.ps1 automation report --macos <macos-json> --windows <copied-windows-json>
.\atrium.ps1 status
.\atrium.ps1 status --json
.\atrium.ps1 logs
.\atrium.ps1 logs --json
.\atrium.ps1 report
.\atrium.ps1 report --bundle
.\atrium.ps1 stop
```

Windows native setup จะพยายามเตรียม Git, Python 3 ที่รันได้จริง, Node.js, uv, pnpm, Docker Desktop, Chrome/Edge/Brave/Chromium, Claude Code CLI, dependencies, env, backend/frontend และเปิด `http://127.0.0.1:5173` ถ้าติด UAC, firewall, Docker first-run หรือ provider login ให้ผู้ใช้กดยืนยันเองแล้วรันคำสั่งเดิมซ้ำ ทั้ง installer และ `.\atrium.ps1 setup` จะตรวจ Python 3 แบบรันจริง ไม่เชื่อแค่ Windows Store alias และจะเพิ่ม PATH ที่จำเป็นทั้งใน session ปัจจุบันและ user PATH เพื่อให้ PowerShell ใหม่ยังเรียก `uv`, `pnpm`, `docker`, `claude` และ `.\atrium.ps1` workflow ได้ต่อ
ถ้า browser install ถูก policy ของเครื่องบล็อก ให้ติดตั้ง Chrome, Edge, Brave หรือ Chromium เอง แล้วรัน `.\atrium.ps1 automation status --commands` เพื่อดู browser/desktop gap ต่อ

`.\atrium.ps1 start` จะพยายามเปิด Docker Desktop และรอ Docker ให้พร้อมก่อนเริ่ม Postgres/Ollama ถ้า Docker ยังไม่พร้อมจะหยุดพร้อม next step ชัดเจนแทนการปล่อยให้ backend fail เงียบ ๆ

`.\atrium.ps1 stop` และ `.\atrium.ps1 restart` ใช้ PID files และหยุด process tree ของ frontend/backend บน Windows เพื่อช่วยลด process ลูกหรือ port listener ค้างหลัง restart
ถ้า backend/frontend ถูกเปิดจาก terminal หรือ tool อื่น `.\atrium.ps1 status` จะยังแสดง listener จริง ส่วน `stop/restart` จะควบคุมเฉพาะ process ที่ ATRIUM native launcher เปิดและมี PID file เท่านั้น

`.\atrium.ps1 tools status` และ `.\atrium.ps1 tools catalog` ตรวจ AI tool registry, tool catalog, risk/executor summary และ connector readiness จาก backend โดยตรง; เพิ่ม `--json` เพื่อส่ง redacted machine-readable diagnostics

`.\atrium.ps1 provider status --probe` ตรวจ ChatGPT account และ Claude Code account จาก backend truth; เพิ่ม `--json` เพื่อดู redacted provider-auth payload สำหรับ debug ส่วน `.\atrium.ps1 provider login ...` เริ่ม login จาก PowerShell โดยตรงและรอจน provider พร้อมถ้าทำได้

`.\atrium.ps1 automation status --commands` แสดงสถานะ browser/desktop HostBridge, owner permission mode และคำสั่ง parity proof ที่ต้องใช้; เพิ่ม `--json` เพื่อดึง automation permission/parity state แบบ redacted จาก PowerShell ส่วน `automation handoff --macos <macos-json>` validate macOS artifact กับ source ปัจจุบันแล้วเขียน packet คำสั่ง Windows/report/audit สำหรับส่งต่อ โดยยังไม่ถือว่า verified; `automation windows-live-proof` เป็น runner สำหรับ Windows interactive desktop session ที่ตรวจ source fingerprint, source manifest, file count, รัน full live probe และ validate artifact ก่อนส่งกลับมาทำ cross-OS report; `automation artifact` ใช้ validate proof artifact ผ่าน native CLI
OpenClaw-level gate ยังนับ MCP external tools เป็น required surface: local MCP fallback ใช้ดูสถานะ/อ่านข้อมูลบางส่วนได้ แต่ไม่ถือว่าผ่าน external-write parity จนกว่า MCP gateway จะพร้อม

`.\atrium.ps1 status` และ `.\atrium.ps1 report` จะรวมสถานะ AI tools, provider auth, permission mode, connector readiness, browser/desktop HostBridge, full-autonomy permission และ cross-OS parity gap เพื่อให้เห็นว่า Windows native automation พร้อมจริงหรือยัง; `status --json` และ `logs --json` ใช้เก็บ runtime/log truth แบบ redacted ได้จาก PowerShell
ถ้าสั่งอัปเดตระบบจาก UI บน Windows backend จะ schedule restart ผ่าน `.\atrium.ps1 restart --force` ใน PowerShell และเขียนผลไว้ที่ `system/logs/self-update-restart.log`
`report` จะรวม Docker CLI/Compose/daemon status, automation proof commands และ redact secret ก่อนพิมพ์ออกมา; `report --bundle` จะสร้าง zip ที่มี redacted support report, backend/frontend logs และ diagnostics JSON สำหรับ status/logs/permission/provider/tools/automation เพื่อส่ง debug

ห้ามวาง repo/runtime ใน OneDrive, Desktop, Documents หรือ Downloads เพราะ database, Docker volume, `node_modules`, virtualenv และ runtime files ไม่ควรอยู่ในโฟลเดอร์ sync หรือ user-folder ที่โดน policy บ่อย

#### ให้ Codex/Claude ทำให้ด้วย Windows native

เปิด Codex หรือ Claude Code บนเครื่อง Windows ใน Coding/Code mode พร้อม Full Access แล้วส่งข้อความนี้:

```text
ติดตั้ง ATRIUM บน Windows native PowerShell จาก https://github.com/Phonsadboy/ATRIUM-TH.git
ใช้ .\atrium.ps1 เป็น runtime-control path หลัก
เตรียม Docker Desktop สำหรับ Docker-backed services และ Claude Code CLI สำหรับ Claude account provider ถ้าจำเป็น
clone repo ไว้ในโฟลเดอร์ที่ไม่ใช่ OneDrive, Desktop, Documents หรือ Downloads
หลังติดตั้งให้รัน .\atrium.ps1 setup --yes แล้วตรวจด้วย .\atrium.ps1 provider status --probe, .\atrium.ps1 automation status --commands, .\atrium.ps1 automation audit และ .\atrium.ps1 status
ห้ามสรุปว่า Windows เป็น OpenClaw-level จนกว่าจะติดตั้ง live macOS/Windows proof artifacts ด้วย .\atrium.ps1 automation report แล้ว .\atrium.ps1 automation audit ผ่าน
เป้าหมายคือเปิด http://127.0.0.1:5173 จาก Windows browser ให้ใช้งานได้จริง
ห้ามขอหรือพิมพ์ password, API key, OAuth token หรือ secret ในแชต
ถ้าต้อง restart Windows, เปิด Docker Desktop, กด UAC/firewall หรือ login provider ให้บอกฉันชัดเจนแล้วรอ
```

เมื่อพร้อม ให้เปิด:

```text
http://127.0.0.1:5173
```

## สิ่งที่ควรเตรียม

- Mac หรือ Windows 1 เครื่อง
- macOS: แนะนำ Apple Silicon และ RAM อย่างน้อย 16GB
- Windows: แนะนำ Windows 11, PowerShell, Docker Desktop, Git, Node.js, uv, pnpm, Chrome/Edge/Brave/Chromium, Claude Code CLI และ RAM อย่างน้อย 16GB
- พื้นที่ว่างอย่างน้อย 20GB
- สิทธิ์เข้า GitHub repo `Phonsadboy/ATRIUM-TH`
- บัญชี Claude หรือ ChatGPT อย่างน้อย 1 บัญชี หรือ API key ของ OpenAI/Anthropic

ตัว installer จะพยายามติดตั้ง Docker Desktop, Git, Python/uv, Node.js, `pnpm`, browser ที่รองรับ และ Claude Code CLI ให้เองถ้ายังไม่มี แต่บางขั้นตอนอาจต้องให้ผู้ใช้ใส่รหัสเครื่องหรือกดยืนยันตามหน้าต่างของ macOS/Windows

ตำแหน่งติดตั้งที่แนะนำ:

```text
macOS: ~/Projects/ai-company
Windows: %USERPROFILE%\Projects\ai-company
```

อย่าวาง repo ใน iCloud Drive และควรเลี่ยง `Desktop` / `Documents` ถ้าเครื่องเปิด iCloud sync เพราะ database, Docker volume, `node_modules`, virtualenv และ runtime files ไม่ควรอยู่ในโฟลเดอร์ที่ sync กับ iCloud

บน Windows ให้ clone repo ใน path local ที่ไม่อยู่ใน OneDrive, Desktop, Documents หรือ Downloads

## คำสั่งหลัก

ตัวอย่างด้านล่างเป็นคำสั่งฝั่ง macOS/Linux shell; บน Windows native ให้ใช้ `.\atrium.ps1` แทน `./atrium`

```bash
./atrium setup
```

คำสั่งหลักสำหรับผู้ใช้ทั่วไป: ตรวจเครื่อง, ติดตั้ง dependency ที่ขาด, เตรียม Postgres/Ollama/backend/frontend, start ระบบ, ตรวจสถานะ และเปิด browser

```bash
./atrium doctor
```

ตรวจเครื่องและสถานะปัจจุบันโดยไม่แก้ไฟล์หรือ start service

```bash
./atrium bootstrap --full
```

เตรียม full stack: env defaults, dependencies, Postgres/pgvector, Ollama, backend migration และ frontend dependencies

```bash
./atrium start
```

เปิด backend และ frontend ใน detached local sessions: macOS ใช้ `screen`; Windows native ใช้ PID files และ logs ใน `system/logs`

```bash
./atrium status
```

ตรวจ Docker, ports, `/health`, `/api/runtime`, `/api/provider-auth/status`, provider readiness และ URL ใช้งาน

```bash
./atrium logs
./atrium report
./atrium stop
```

ดู log, สร้าง support report แบบ redact secrets, หรือหยุดเฉพาะ ATRIUM-owned local sessions

## ผลลัพธ์ที่ควรได้

- Repo อยู่ที่ `~/Projects/ai-company`
- Docker containers สำหรับ Postgres, Ollama ทำงานอยู่
- Ollama มี model `bge-m3`
- Backend เปิดที่ `http://127.0.0.1:8787`
- Frontend เปิดที่ `http://127.0.0.1:5173`
- หน้า ATRIUM เปิดใน browser ได้
- มี provider อย่างน้อย 1 ตัวที่พร้อมตอบงานจริง หรือ `./atrium status` บอก blocker ชัดเจน

## ถ้าติด blocker

เรื่องที่ CLI/agent อาจทำแทนไม่ได้ทั้งหมด:

- ใส่ password/admin ของเครื่อง
- เปิด Docker Desktop ครั้งแรก
- login Claude หรือ ChatGPT
- ใส่ API key
- กดอนุญาต macOS Privacy & Security
- กดอนุญาต Windows Firewall/UAC
- ขอสิทธิ์เข้า private GitHub repo

หลังแก้ blocker แล้วให้รัน:

```bash
./atrium setup
```

บน Windows native ให้ใช้ `.\atrium.ps1 start`; ถ้าติด blocker เฉพาะเครื่องให้แก้ dependency/permission บน PowerShell แล้วรันคำสั่งเดิมซ้ำ

หรือบอก agent:

```text
ทำต่อจากจุดที่ค้าง ตรวจสอบ full stack ซ้ำ แล้วเปิด ATRIUM ให้พร้อมใช้งาน
```

## What ATRIUM Is

ATRIUM is a workspace for running a personal “AI company” on your Mac or Windows workstation. Windows uses a native PowerShell runtime-control path. It is not just a library where you wire agents together from scratch. You talk to an Executive, then ATRIUM can route work to departments such as research, engineering, content, operations, or custom teams you create. Each department has its own role, tools, memory, assigned work, artifacts, and visible runtime status.

The project is shaped by a practical gap in many AI agent and multi-agent demos: the idea is exciting, but real use still requires you to assemble the backend, frontend, database, runtime, provider auth, memory, tool permissions, logs, health checks, and operating guide yourself. ATRIUM starts as a full-stack local app so you can open a browser and inspect a working AI company, not just watch agents talk in a terminal.

### Why ATRIUM Is Different

- **Thai-first**: onboarding, prompts, and day-to-day workflow are designed around Thai users first.
- **Local-first on Mac/Windows**: runtime data, memory, database files, and workspaces are primarily kept on your machine. Windows uses native PowerShell for runtime control and Docker Desktop for Docker-backed services.
- **More than agent chat**: ATRIUM includes an Executive, departments, tasks, approvals, handoffs, memory, artifacts, and runtime health.
- **Full stack included**: FastAPI backend, Vite frontend, Postgres/pgvector, Ollama, and setup shortcuts through `./atrium` on macOS or `.\atrium.ps1` on Windows.
- **Flexible provider paths**: Claude Code account, ChatGPT account OAuth, OpenAI Platform API key, Anthropic API key, and local embeddings through Ollama.
- **Runtime truth over guesswork**: `./atrium doctor` / `.\atrium.ps1 doctor`, `./atrium status` / `.\atrium.ps1 status`, `/health`, `/api/runtime`, and redacted support reports help identify what is ready and what is blocked.
- **Human control stays visible**: important actions should have approvals, credential readiness, and inspectable status instead of invisible autonomy.

### Compared With Generic Agent Frameworks

CrewAI, LangGraph, AutoGen/Microsoft Agent Framework, and MetaGPT show important patterns: agents need focused roles, workflows need state, teams need orchestration, and complex work needs structure. Many of those projects are still primarily developer frameworks. You still decide where state lives, how the UI works, how secrets are handled, how health is checked, and how a new user gets from clone to a running system.

ATRIUM focuses on the product side of that problem: turning a multi-agent idea into a local workspace with a UI, setup script, runtime checks, provider readiness, and Thai-first onboarding. It is not magic autopilot. You still need AI accounts, Docker, permissions, and human review for important work. The goal is to make the path from “I want to try an AI company” to “it is running on my own machine” as short and concrete as possible.

## English Installation

Choose your OS first, then choose an installation style:

- **Manual**: copy and run the commands yourself.
- **Codex/Claude assisted**: let Codex or Claude Code run the installation while you wait and handle only OS/provider prompts.

For the assisted path, use Codex or Claude Code in **Coding / Code mode** with **Full Access** or equivalent permissions: file read/write, terminal commands, network access, and browser access. Do not paste passwords, API keys, OAuth tokens, or secrets into chat.

### macOS

Use `~/Projects/ai-company`. Avoid iCloud Drive, Desktop, or Documents if those folders sync with iCloud.

#### Option 1: Manual

Open Terminal and run:

```bash
curl -fsSL https://raw.githubusercontent.com/Phonsadboy/ATRIUM-TH/main/ops/install_macos.sh -o /tmp/atrium-install-macos.sh && bash /tmp/atrium-install-macos.sh
```

If the repo is already cloned:

```bash
cd ~/Projects/ai-company
./atrium setup
```

#### Option 2: Codex/Claude Assisted

Open Codex or Claude Code in Coding/Code mode with Full Access and send:

```text
Install ATRIUM on macOS from https://github.com/Phonsadboy/ATRIUM-TH.git.
Use ~/Projects/ai-company.
Do not place the repo in iCloud Drive, Desktop, or Documents if those folders sync with iCloud.
Clone the repo if needed, then run ./atrium setup --yes.
Finish by running ./atrium status.
The goal is to open http://127.0.0.1:5173 and make the full stack usable.
Never ask for or print passwords, API keys, OAuth tokens, or secrets in chat.
If Docker Desktop, machine password, provider login, or macOS permission approval is needed, tell me exactly what to do and wait.
```

### Windows Native PowerShell

From a Windows checkout, run:

```powershell
.\atrium.ps1 doctor
.\atrium.ps1 bootstrap --full
.\atrium.ps1 setup
.\atrium.ps1 start
.\atrium.ps1 restart
.\atrium.ps1 tools status
.\atrium.ps1 tools status --json
.\atrium.ps1 tools catalog
.\atrium.ps1 tools catalog --json
.\atrium.ps1 provider status --probe
.\atrium.ps1 provider status --probe --json
.\atrium.ps1 provider login chatgpt
.\atrium.ps1 provider login claude-code
.\atrium.ps1 provider disconnect chatgpt
.\atrium.ps1 automation status --commands
.\atrium.ps1 automation status --json
.\atrium.ps1 automation source
.\atrium.ps1 automation audit
.\atrium.ps1 automation handoff --macos <macos-json>
.\atrium.ps1 automation windows-live-proof --parity-run-id <run-id> --source-fingerprint <fingerprint> --source-manifest-sha256 <manifest> --source-file-count <count>
.\atrium.ps1 automation artifact --label windows --expect-parity-run-id <run-id> <windows-json>
.\atrium.ps1 automation report --macos <macos-json> --windows <copied-windows-json>
.\atrium.ps1 status
.\atrium.ps1 status --json
.\atrium.ps1 logs
.\atrium.ps1 logs --json
.\atrium.ps1 report
.\atrium.ps1 report --bundle
.\atrium.ps1 stop
```

The native path controls backend/frontend directly from Windows PowerShell and uses Docker Desktop for Postgres/Ollama.
`atrium.cmd` is also available for Windows Terminal/cmd.exe and forwards to `.\atrium.ps1` with ExecutionPolicy Bypass; the documented primary path remains PowerShell.
The installer and `.\atrium.ps1 setup` verify a runnable Python 3 command rather than trusting the Windows Store alias, refresh PATH for the current session, and persist common user PATH entries for `uv`, `pnpm`, `docker`, and `claude` so a new PowerShell can keep using the same native workflow.
If browser installation is blocked by local policy, install Chrome, Edge, Brave, or Chromium manually, then run `.\atrium.ps1 automation status --commands` to inspect remaining browser/desktop gaps.
`.\atrium.ps1 start` attempts to open Docker Desktop and wait for Docker before starting Postgres/Ollama; if Docker is still blocked, it stops with an explicit next step instead of letting the backend fail later.
`.\atrium.ps1 tools status` and `.\atrium.ps1 tools catalog` inspect the AI tool registry, tool catalog, risk/executor summary, and connector readiness directly from the backend; add `--json` for redacted machine-readable diagnostics.
`.\atrium.ps1 provider ...` manages ChatGPT account and Claude Code account readiness from the native terminal; add `--json` to `provider status --probe` for a redacted debug payload.
`.\atrium.ps1 automation ...` exposes HostBridge browser/desktop readiness, source provenance handoff, a Windows proof handoff packet from a validated macOS artifact, native artifact validation, the preferred Windows live proof runner, the cross-OS report installer, and the OpenClaw-level audit gate from the same native entrypoint.
The OpenClaw-level gate treats MCP external tools as a required surface: local MCP fallback can provide read/status guidance, but it does not satisfy external-write parity until the MCP gateway is healthy.
`.\atrium.ps1 automation status --commands` prints browser/desktop HostBridge readiness, owner permission mode, and parity proof commands; add `--json` to capture redacted automation permission/parity state from PowerShell.
`.\atrium.ps1 status` and `.\atrium.ps1 report` include AI tool, provider-auth, permission mode, connector, browser/desktop HostBridge, full-autonomy permission, and cross-OS parity readiness summaries; `status --json` and `logs --json` capture redacted runtime/log truth from PowerShell.
When the UI self-update flow runs on Windows, ATRIUM schedules restart through `.\atrium.ps1 restart --force` in PowerShell and writes the result to `system/logs/self-update-restart.log`.
`report` includes Docker CLI/Compose/daemon status, automation proof commands, and redacts secrets before printing; `report --bundle` writes a redacted zip with the support report, backend/frontend logs, and status/logs/permission/provider/tools/automation diagnostics JSON for debugging.

For a fresh native Windows install before the repo exists:

```powershell
$script="$env:TEMP\atrium-windows-native-install.ps1"; Invoke-WebRequest -UseBasicParsing "https://raw.githubusercontent.com/Phonsadboy/ATRIUM-TH/main/ops/install_windows_native.ps1" -OutFile $script; powershell -ExecutionPolicy Bypass -File $script
```

Temporary skip flags are available: `-NoStart`, `-SkipDockerInstall`, `-SkipBrowserInstall`, and `-SkipClaudeCodeInstall`.

#### Codex/Claude Assisted Native Windows

Open Codex or Claude Code on the Windows machine in Coding/Code mode with Full Access and send:

```text
Install ATRIUM on Windows native PowerShell from https://github.com/Phonsadboy/ATRIUM-TH.git.
Use .\atrium.ps1 as the main runtime-control path.
Prepare Docker Desktop for Docker-backed services and Claude Code CLI for the Claude account provider if needed.
Clone the repo into a folder outside OneDrive, Desktop, Documents, and Downloads.
After setup, run .\atrium.ps1 setup --yes and verify with .\atrium.ps1 provider status --probe, .\atrium.ps1 automation status --commands, .\atrium.ps1 automation audit, and .\atrium.ps1 status. Do not claim OpenClaw-level Windows parity until live macOS/Windows proof artifacts have been installed with .\atrium.ps1 automation report and audit passes.
The goal is to open http://127.0.0.1:5173 from a Windows browser and make the full stack usable.
Never ask for or print passwords, API keys, OAuth tokens, or secrets in chat.
If Windows restart, Docker Desktop, UAC/firewall approval, or provider login is needed, tell me exactly what to do and wait.
```

Open:

```text
http://127.0.0.1:5173
```

## Windows Notes

- Windows native uses `.\atrium.ps1` for setup/start/stop/status/log/report from PowerShell.
- Docker Desktop must be running on Windows for the full Docker-backed stack.
- Keep native Windows checkouts out of OneDrive, Desktop, Documents, and Downloads.
- Windows browser/desktop automation must run from a signed-in interactive desktop session; service-only sessions cannot drive the visible desktop.
