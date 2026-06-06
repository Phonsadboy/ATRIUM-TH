# ATRIUM on Mac / Windows

ATRIUM is a Thai-first, local-first full-stack AI company workspace for macOS and Windows through WSL2. This repo includes the FastAPI backend, Vite frontend, Docker services, runtime configuration, and a shortcut CLI for setting up the local full stack.

Repository:

```text
https://github.com/Phonsadboy/ATRIUM-TH.git
```

## โปรเจ็กนี้คืออะไร

ATRIUM คือ workspace สำหรับสร้าง “บริษัท AI ส่วนตัว” บนเครื่องของคุณ ไม่ใช่แค่ library สำหรับประกอบ agent เองจากศูนย์ ผู้ใช้คุยกับ Executive แล้วให้ระบบแตกงานไปยังแผนกต่าง ๆ เช่น research, engineering, content, operations หรือทีมที่คุณสร้างเอง แต่ละแผนกมีบทบาท เครื่องมือ หน่วยความจำ งานที่ได้รับมอบหมาย และสถานะการทำงานที่ตรวจสอบได้

แนวคิดของโปรเจ็กนี้มาจากปัญหาที่เจอบ่อยในโลก AI agent/multi-agent: demo ดูน่าตื่นเต้น แต่พอจะใช้จริงต้องประกอบ backend, frontend, database, runtime, provider auth, memory, tool permissions, logs, health checks และ runbook เองทั้งหมด ATRIUM เลยเลือกเป็น full-stack local app ตั้งแต่แรก เปิด browser แล้วเห็นบริษัท AI กำลังทำงานจริง ไม่ใช่แค่ agents คุยกันใน terminal

### จุดเด่นของ ATRIUM

- **Thai-first**: ออกแบบให้ผู้ใช้ไทยเริ่มได้ง่ายขึ้น ทั้ง prompt, onboarding และ flow การใช้งาน
- **Local-first บน Mac/Windows**: ข้อมูล runtime, memory, database และ workspace อยู่ในเครื่องคุณเป็นหลัก โดย Windows แนะนำให้ใช้ WSL2/Ubuntu
- **AI company ไม่ใช่แค่ agent chat**: มี Executive, แผนก, task, approval, handoff, memory, artifacts และสถานะ runtime
- **Full stack พร้อมใช้**: FastAPI backend, Vite frontend, Postgres/pgvector, Ollama และ setup shortcut ผ่าน `./atrium`
- **ใช้ provider ได้หลายแบบ**: Claude Code account, ChatGPT account OAuth, OpenAI Platform API key, Anthropic API key และ local embedding ผ่าน Ollama
- **เห็นความจริงของระบบ**: `./atrium doctor`, `./atrium status`, `/health`, `/api/runtime` และ redacted support report ช่วยแยกว่าอะไรพร้อม อะไรยังติด blocker
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
- ถ้า macOS/Windows ขอ password, restart, permission, firewall, Docker Desktop, WSL integration หรือ provider login ให้ผู้ใช้ทำเอง แล้วบอก agent ว่าเสร็จแล้ว

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

### Windows ผ่าน WSL2/Ubuntu

Windows ต้องใช้ WSL2 + Ubuntu + Docker Desktop with WSL integration เป็นทางหลัก อย่า clone repo ไว้ใน OneDrive, Desktop, Documents หรือ `/mnt/c/...` เพราะ Docker volume, database, `node_modules`, virtualenv และ runtime files ควรอยู่ใน filesystem ของ WSL เอง

#### วิธีที่ 1: ทำเอง

เปิด PowerShell แบบ Administrator แล้วรัน:

```powershell
$script="$env:TEMP\atrium-windows-install.ps1"; Invoke-WebRequest -UseBasicParsing "https://raw.githubusercontent.com/Phonsadboy/ATRIUM-TH/main/ops/windows_wsl_install.ps1" -OutFile $script; powershell -ExecutionPolicy Bypass -File $script
```

สคริปต์นี้จะติดตั้ง/ตรวจ WSL Ubuntu, ติดตั้ง Docker Desktop ผ่าน `winget` ถ้ายังไม่มี, เตรียม dependency ใน Ubuntu, clone repo ไปที่ `~/Projects/ai-company`, รัน `./atrium setup --yes` แล้วเปิดระบบให้ ถ้า Windows ขอ restart หรือ Ubuntu ขอสร้าง UNIX user ให้ทำขั้นตอนนั้นให้เสร็จแล้วรันคำสั่งเดิมซ้ำ

ถ้า clone repo ฝั่ง Windows ไว้แล้ว ให้รันจาก PowerShell ใน repo นั้น:

```powershell
powershell -ExecutionPolicy Bypass -File .\atrium-windows.ps1
```

หลังติดตั้งเสร็จ ถ้าต้อง setup/start ซ้ำเองใน WSL ให้ใช้:

```bash
cd ~/Projects/ai-company
./atrium setup
```

#### วิธีที่ 2: ให้ Codex/Claude ทำให้

เปิด Codex หรือ Claude Code บนเครื่อง Windows ใน Coding/Code mode พร้อม Full Access แล้วส่งข้อความนี้:

```text
ติดตั้ง ATRIUM บน Windows ผ่าน WSL2/Ubuntu จาก https://github.com/Phonsadboy/ATRIUM-TH.git
ใช้สคริปต์ PowerShell/WSL installer ของ repo เป็นหลัก
เตรียม Docker Desktop และเปิด WSL integration ให้ Ubuntu ถ้าจำเป็น
clone repo ไว้ใน WSL ที่ ~/Projects/ai-company เท่านั้น
ห้ามวาง repo ใน OneDrive, Desktop, Documents หรือ /mnt/c/...
หลังติดตั้งให้รัน ./atrium setup --yes และตรวจสอบด้วย ./atrium status
เป้าหมายคือเปิด http://127.0.0.1:5173 จาก Windows browser ให้ใช้งานได้จริง
ห้ามขอหรือพิมพ์ password, API key, OAuth token หรือ secret ในแชต
ถ้าต้อง restart Windows, สร้าง Ubuntu user, เปิด Docker Desktop, เปิด WSL integration, กด UAC/firewall หรือ login provider ให้บอกฉันชัดเจนแล้วรอ
```

เมื่อพร้อม ให้เปิด:

```text
http://127.0.0.1:5173
```

## สิ่งที่ควรเตรียม

- Mac หรือ Windows 1 เครื่อง
- macOS: แนะนำ Apple Silicon และ RAM อย่างน้อย 16GB
- Windows: แนะนำ Windows 11, WSL2 + Ubuntu, Docker Desktop with WSL integration และ RAM อย่างน้อย 16GB
- พื้นที่ว่างอย่างน้อย 20GB
- สิทธิ์เข้า GitHub repo `Phonsadboy/ATRIUM-TH`
- บัญชี Claude หรือ ChatGPT อย่างน้อย 1 บัญชี หรือ API key ของ OpenAI/Anthropic

ตัว installer จะพยายามติดตั้ง Docker Desktop, Git, Python/uv, Node.js และ `pnpm` ให้เองถ้ายังไม่มี แต่บางขั้นตอนอาจต้องให้ผู้ใช้ใส่รหัสเครื่องหรือกดยืนยันตามหน้าต่างของ macOS/Windows

ตำแหน่งติดตั้งที่แนะนำ:

```text
macOS: ~/Projects/ai-company
Windows WSL2: ~/Projects/ai-company
```

อย่าวาง repo ใน iCloud Drive และควรเลี่ยง `Desktop` / `Documents` ถ้าเครื่องเปิด iCloud sync เพราะ database, Docker volume, `node_modules`, virtualenv และ runtime files ไม่ควรอยู่ในโฟลเดอร์ที่ sync กับ iCloud

บน Windows ให้ clone repo ใน filesystem ของ WSL เช่น `/home/<user>/Projects/ai-company` ไม่ใช่ `/mnt/c/Users/...`, OneDrive, Desktop หรือ Documents

## คำสั่งหลัก

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

เปิด backend และ frontend ใน detached `screen` sessions ใช้ได้ดีบน macOS หรือ WSL ที่มี `screen` และ `zsh` พร้อม ถ้า Windows/WSL ไม่มี `screen` หรือ `zsh` ให้รัน backend และ frontend แยก terminal ตามตัวอย่างด้านบน

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
- เปิด Docker Desktop WSL integration บน Windows
- login Claude หรือ ChatGPT
- ใส่ API key
- กดอนุญาต macOS Privacy & Security
- กดอนุญาต Windows Firewall/UAC
- ขอสิทธิ์เข้า private GitHub repo

หลังแก้ blocker แล้วให้รัน:

```bash
./atrium setup
```

บน Windows/WSL ถ้า `./atrium start` ติด `screen` หรือ `zsh` ให้เปิด backend และ frontend แยก 2 terminal ตามตัวอย่าง Windows ด้านบนแทน

หรือบอก agent:

```text
ทำต่อจากจุดที่ค้าง ตรวจสอบ full stack ซ้ำ แล้วเปิด ATRIUM ให้พร้อมใช้งาน
```

## What ATRIUM Is

ATRIUM is a workspace for running a personal “AI company” on your Mac or Windows workstation. The recommended Windows path is WSL2/Ubuntu with Docker Desktop WSL integration. It is not just a library where you wire agents together from scratch. You talk to an Executive, then ATRIUM can route work to departments such as research, engineering, content, operations, or custom teams you create. Each department has its own role, tools, memory, assigned work, artifacts, and visible runtime status.

The project is shaped by a practical gap in many AI agent and multi-agent demos: the idea is exciting, but real use still requires you to assemble the backend, frontend, database, runtime, provider auth, memory, tool permissions, logs, health checks, and operating guide yourself. ATRIUM starts as a full-stack local app so you can open a browser and inspect a working AI company, not just watch agents talk in a terminal.

### Why ATRIUM Is Different

- **Thai-first**: onboarding, prompts, and day-to-day workflow are designed around Thai users first.
- **Local-first on Mac/Windows**: runtime data, memory, database files, and workspaces are primarily kept on your machine. Windows should use WSL2/Ubuntu for the full local stack.
- **More than agent chat**: ATRIUM includes an Executive, departments, tasks, approvals, handoffs, memory, artifacts, and runtime health.
- **Full stack included**: FastAPI backend, Vite frontend, Postgres/pgvector, Ollama, and a setup shortcut through `./atrium`.
- **Flexible provider paths**: Claude Code account, ChatGPT account OAuth, OpenAI Platform API key, Anthropic API key, and local embeddings through Ollama.
- **Runtime truth over guesswork**: `./atrium doctor`, `./atrium status`, `/health`, `/api/runtime`, and redacted support reports help identify what is ready and what is blocked.
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

### Windows Through WSL2/Ubuntu

Use WSL2 + Ubuntu + Docker Desktop with WSL integration. Keep the repo inside the WSL filesystem, for example `/home/<user>/Projects/ai-company`; avoid OneDrive, Desktop, Documents, and `/mnt/c/...`.

#### Option 1: Manual

Open Administrator PowerShell and run:

```powershell
$script="$env:TEMP\atrium-windows-install.ps1"; Invoke-WebRequest -UseBasicParsing "https://raw.githubusercontent.com/Phonsadboy/ATRIUM-TH/main/ops/windows_wsl_install.ps1" -OutFile $script; powershell -ExecutionPolicy Bypass -File $script
```

Shortcut from an already cloned Windows checkout:

```powershell
powershell -ExecutionPolicy Bypass -File .\atrium-windows.ps1
```

After the shortcut is done, start manually from WSL when needed:

```bash
cd ~/Projects/ai-company
./atrium setup
```

#### Option 2: Codex/Claude Assisted

Open Codex or Claude Code on the Windows machine in Coding/Code mode with Full Access and send:

```text
Install ATRIUM on Windows through WSL2/Ubuntu from https://github.com/Phonsadboy/ATRIUM-TH.git.
Use the repo's PowerShell/WSL installer as the main path.
Prepare Docker Desktop and enable WSL integration for Ubuntu if needed.
Clone the repo inside WSL at ~/Projects/ai-company only.
Do not place the repo in OneDrive, Desktop, Documents, or /mnt/c/...
After setup, run ./atrium setup --yes and verify with ./atrium status.
The goal is to open http://127.0.0.1:5173 from a Windows browser and make the full stack usable.
Never ask for or print passwords, API keys, OAuth tokens, or secrets in chat.
If Windows restart, Ubuntu user creation, Docker Desktop, WSL integration, UAC/firewall approval, or provider login is needed, tell me exactly what to do and wait.
```

Open:

```text
http://127.0.0.1:5173
```

## Windows Notes

- The supported Windows path is WSL2/Ubuntu. Native PowerShell can be used for manual experiments, but `./atrium start` is a Unix-style shortcut that expects `screen` and `zsh`.
- Docker Desktop must be running on Windows, and WSL integration must be enabled for the Ubuntu distro that contains this repo.
- Keep the repo inside the WSL filesystem. Avoid `/mnt/c/...`, OneDrive, Desktop, and Documents for runtime-heavy files.
- If `./atrium start` is blocked by missing `screen` or `zsh`, rerun `.\atrium-windows.ps1` or install those packages inside WSL with `sudo apt install -y screen zsh`.
