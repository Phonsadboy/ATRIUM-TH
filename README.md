# ATRIUM on Mac: ติดตั้ง Full Stack ด้วย Prompt เดียว

คู่มือนี้ออกแบบให้ผู้ใช้ไทยเป็นหลัก โดยให้ภาษาไทยเป็นคู่มือหลัก และมี English guide สำหรับผู้ใช้ที่ถนัดภาษาอังกฤษอยู่ด้านล่าง ทั้งสองภาษาใช้เป้าหมายเดียวกัน: เปิด Codex หรือ Claude Code แล้วให้ agent ติดตั้ง ATRIUM full stack จนพร้อมใช้งาน

This guide is designed primarily for Thai users. An English guide is included below for users who prefer English. Both guides have the same goal: open Codex or Claude Code and let the agent install the ATRIUM full stack until it is ready to use.

Repository:

```text
https://github.com/Phonsadboy/ATRIUM-TH.git
```

## Repository Description

ATRIUM is a Thai-first, local-first full-stack AI company workspace for Mac. This repository includes the FastAPI backend, Vite frontend, Docker services, runtime configuration, and beginner-friendly one-prompt setup guides for installing Postgres/pgvector, Ollama, Letta, and the web app together from the start.

---

## ภาษาไทย: ใช้งานแบบเร็วที่สุด

1. เปิด Codex หรือ Claude Code บน Mac เครื่องที่จะติดตั้ง
2. Copy prompt ภาษาไทยในหัวข้อถัดไปไปวาง
3. รอให้ agent ติดตั้ง full stack และตรวจสอบระบบ
4. ถ้า agent ขอให้ login, ใส่ API key, เปิด Docker Desktop หรือให้สิทธิ์ macOS ให้ทำตาม
5. เมื่อเสร็จ ให้เข้าใช้งานที่ `http://127.0.0.1:5173`

### สิ่งที่ควรเตรียม

- Mac 1 เครื่อง แนะนำ Apple Silicon และ RAM อย่างน้อย 16GB
- พื้นที่ว่างอย่างน้อย 20GB
- สิทธิ์เข้า GitHub repo `Phonsadboy/ATRIUM-TH`
- บัญชี Claude หรือ ChatGPT อย่างน้อย 1 บัญชี
- ถ้าใช้ API key แทน account login ให้เตรียม OpenAI Platform API key หรือ Anthropic API key

### ตำแหน่งติดตั้งที่ถูกต้อง

ให้ติดตั้งไว้ที่:

```text
~/Projects/ai-company
```

ห้ามติดตั้งไว้ใน iCloud Drive และควรเลี่ยง `Desktop` / `Documents` ถ้าเครื่องเปิด iCloud sync สำหรับสองโฟลเดอร์นี้ เพราะ database, Docker volume, `node_modules`, virtualenv และ runtime files ไม่ควรอยู่ในโฟลเดอร์ที่ sync กับ iCloud

---

## Prompt ภาษาไทยสำหรับ Codex / Claude Code

Copy ทั้งก้อนนี้ไปวาง:

```text
คุณคือ coding agent ที่กำลังติดตั้ง ATRIUM / AI Company แบบ full stack บน Mac เครื่องนี้ให้ผู้ใช้จนพร้อมใช้งานจริง

Repo ที่ต้องติดตั้ง:
https://github.com/Phonsadboy/ATRIUM-TH.git

เป้าหมายสุดท้าย:
- Repo อยู่ที่ ~/Projects/ai-company
- ห้ามวาง repo ใน iCloud Drive
- ห้ามวาง repo ใน Desktop หรือ Documents ถ้าเครื่องเปิด iCloud sync สำหรับโฟลเดอร์เหล่านั้น
- Full stack ต้องพร้อมตั้งแต่แรก: Postgres/pgvector + Ollama + Letta + FastAPI backend + Vite frontend
- Backend เปิดที่ http://127.0.0.1:8787
- Frontend เปิดที่ http://127.0.0.1:5173
- ตรวจ health/runtime/provider ให้เรียบร้อย
- เปิดหน้าเว็บให้ผู้ใช้ใช้งานได้จริง
- ห้ามหยุดแค่แนะนำ ต้องลงมือทำจนกว่าจะพร้อมใช้งาน หรือเจอ blocker ที่ต้องให้ผู้ใช้ login/ใส่ secret/กดอนุญาตเอง

กติกาความปลอดภัย:
- ห้ามพิมพ์ secret, API key, OAuth token หรือ password ลงในแชต
- ถ้าต้องใช้ secret ให้ผู้ใช้ใส่ใน system/.env, macOS Keychain, provider login หรือ OAuth flow เท่านั้น
- ถ้ามี server เดิมรันอยู่ ให้ตรวจสอบก่อน อย่าฆ่า process ที่ไม่เกี่ยวข้อง
- ถ้า repo มี local change อยู่ ห้าม reset, checkout ทับ, หรือลบทิ้งโดยไม่ขออนุญาต
- ถ้าต้องติดตั้งโปรแกรมที่ต้องใช้ password หรือกด GUI ให้บอกผู้ใช้ชัดเจนแล้วรอให้ทำเสร็จ

ขั้นตอนติดตั้ง:

1. ตรวจเครื่องและตำแหน่งติดตั้ง
   - เช็ก macOS, CPU, RAM, shell และ current directory
   - ตรวจว่า path ปัจจุบันหรือ path ที่จะติดตั้งไม่ได้อยู่ใต้ iCloud Drive, ~/Library/Mobile Documents, Desktop ที่ sync กับ iCloud หรือ Documents ที่ sync กับ iCloud
   - ใช้ path ติดตั้งมาตรฐานเท่านั้น: ~/Projects/ai-company
   - สร้าง ~/Projects ถ้ายังไม่มี

2. ตรวจและติดตั้งเครื่องมือพื้นฐาน
   - เช็กว่ามี git, brew, node, pnpm, uv, python, docker, docker compose และ Google Chrome หรือไม่
   - ถ้าไม่มี Homebrew ให้ติดตั้ง Homebrew หรือบอกผู้ใช้ให้ติดตั้งจาก https://brew.sh ถ้าต้องกด password เอง
   - ถ้าขาด ให้ติดตั้งด้วยคำสั่งที่เหมาะสม เช่น:
     brew install git node pnpm uv
     brew install --cask docker
     brew install --cask google-chrome
   - ถ้าติดตั้ง Docker Desktop ใหม่ ให้เปิด Docker Desktop และรอจน Docker พร้อมใช้งานก่อนทำขั้นต่อไป

3. เตรียม repo
   - ถ้ายังไม่มี repo:
     git clone https://github.com/Phonsadboy/ATRIUM-TH.git ~/Projects/ai-company
   - ถ้ามี repo แล้ว:
     cd ~/Projects/ai-company
     git remote -v
     git status --short --branch
   - ถ้า remote ไม่ใช่ https://github.com/Phonsadboy/ATRIUM-TH.git ให้หยุดถามผู้ใช้ก่อน
   - ถ้ามี local changes ให้รายงานและถามก่อน pull หรือแก้ไข
   - ถ้าปลอดภัย ให้รัน git pull

4. เตรียม provider ให้ใช้งานจริง
   - ใช้ provider ที่ผู้ใช้มีจริงอย่างน้อย 1 แบบ:
     Claude Code account
     ChatGPT account OAuth
     OpenAI Platform API key
     Anthropic API key
   - ถ้ามีคำสั่ง claude ให้เช็ก: claude auth status --json
   - ถ้า Claude Code ยังไม่พร้อม และผู้ใช้ต้องการใช้ Claude Code:
     npm install -g @anthropic-ai/claude-code
     แล้วให้ผู้ใช้ทำ: claude setup-token
   - ถ้าผู้ใช้ต้องการใช้ ChatGPT account OAuth ให้รันจาก repo root:
     uv --project system run python ops/chatgpt_account_oauth_login.py
     แล้วให้ผู้ใช้ login ใน browser จนเสร็จ
   - ถ้าผู้ใช้ใช้ OpenAI Platform API key หรือ Anthropic API key ให้ให้ผู้ใช้ใส่ค่าใน system/.env เอง ห้ามพิมพ์ค่า secret ในแชต

5. สร้าง system/.env สำหรับ full stack
   - เข้า ~/Projects/ai-company/system
   - ถ้า system/.env ยังไม่มี ให้ copy จาก template: cp .env.example .env
   - ก่อนตั้งค่า concurrency ให้ถามผู้ใช้ว่าอยากให้ ATRIUM รันงานพร้อมกันกี่งาน พร้อมอธิบายว่า:
     ATRIUM_CHAT_REPLY_WORKER_CONCURRENCY คือจำนวน reply จากห้องแผนกที่รันพร้อมกันได้
     ATRIUM_DEPARTMENT_WORKER_CONCURRENCY คือจำนวนงาน/review step อัตโนมัติของแผนกที่ engine ทำพร้อมกันในแต่ละ tick
     ถ้าไม่แน่ใจให้ใช้ค่า default 5; ถ้า Mac สเปคแรงอาจเลือก 10-20 ได้ตามสมควร แต่จะใช้ CPU, RAM และ provider quota/API มากขึ้น
   - แก้หรือเติมค่า full stack เหล่านี้:
     ATRIUM_AGENT_BACKEND=letta
     ATRIUM_DATABASE_URL=postgresql+asyncpg://atrium:atrium@127.0.0.1:5432/atrium
     ATRIUM_DATA_DIR=./data
     ATRIUM_GRAPH_BACKEND=auto
     ATRIUM_HOST=127.0.0.1
     ATRIUM_PORT=8787
     ATRIUM_CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
     ATRIUM_OLLAMA_BASE_URL=http://127.0.0.1:11434
     ATRIUM_OLLAMA_EMBEDDING_MODEL=bge-m3
     ATRIUM_EMBEDDING_DIM=1024
     ATRIUM_LETTA_BASE_URL=http://127.0.0.1:8283
     ATRIUM_OBJECT_STORE_ENABLED=true
     CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
     ATRIUM_CHAT_REPLY_WORKER_CONCURRENCY=5
     ATRIUM_DEPARTMENT_WORKER_CONCURRENCY=5
   - ถ้ามี OpenAI Platform API key ให้ผู้ใช้ใส่เอง:
     ATRIUM_OPENAI_API_KEY=...
     ATRIUM_OPENAI_BASE_URL=https://api.openai.com/v1
   - ถ้ามี Anthropic API key ให้ผู้ใช้ใส่เอง:
     ATRIUM_ANTHROPIC_AUTH_TOKEN=...
     ATRIUM_ANTHROPIC_BASE_URL=https://api.anthropic.com
   - ถ้าใช้ Anthropic API key กับ Letta ด้วย ให้ใส่ค่า ANTHROPIC_API_KEY ใน environment ที่ Docker Compose อ่านได้โดยไม่พิมพ์ secret ลงแชต

6. ติดตั้ง dependencies
   - Backend:
     cd ~/Projects/ai-company/system
     uv sync --extra live --extra postgres --extra graph
   - Frontend:
     cd ~/Projects/ai-company/ui
     pnpm install
   - สร้างหรือแก้ ui/.env.local:
     VITE_ATRIUM_API_URL=http://127.0.0.1:8787

7. เปิด Docker full stack
   - เข้า repo root: cd ~/Projects/ai-company
   - ตรวจว่า Docker พร้อม: docker info
   - เปิด Postgres และ Ollama: docker compose up -d postgres ollama
   - เปิด Letta: docker compose --profile v2 up -d letta
   - โหลด embedding model: docker compose exec ollama ollama pull bge-m3
   - ตรวจ container: docker compose ps และ docker compose exec ollama ollama list

8. รัน database migration
   - cd ~/Projects/ai-company/system
   - uv run --extra postgres alembic -c alembic.ini upgrade head
   - ถ้า migration fail ให้แก้จาก error จริงและอย่าเดา

9. เปิด backend และ frontend
   - ตรวจพอร์ตด้วย lsof -i :8787 และ lsof -i :5173
   - ถ้าพอร์ตถูกใช้โดย process เก่าของ ATRIUM ให้รายงานก่อนจัดการ
   - เปิด backend:
     cd ~/Projects/ai-company/system
     uv run --extra live --extra postgres --extra graph python -m app
   - เปิด frontend:
     cd ~/Projects/ai-company/ui
     pnpm dev --host 127.0.0.1 --port 5173
   - ใช้ terminal/session แยกกัน หรือ detached process ที่ยังทำงานต่อหลังจบงาน

10. ตรวจสอบว่าพร้อมใช้งานจริง
   - เช็ก:
     curl http://127.0.0.1:8787/health
     curl http://127.0.0.1:8787/api/runtime
     curl http://127.0.0.1:8787/api/provider-auth/status
   - ตรวจว่า runtime ไม่ degraded จาก Postgres/Ollama/Letta
   - เปิด http://127.0.0.1:5173
   - ทดสอบ Executive chat หรือบอกผู้ใช้ให้ลองส่ง:
     ช่วยสรุปว่าระบบตอนนี้พร้อมใช้งานส่วนไหนบ้าง

11. ถ้าต้องใช้สิทธิ์ macOS
   - ถ้า browser/desktop automation ใช้ไม่ได้ ให้บอกผู้ใช้เปิด System Settings > Privacy & Security
   - ให้สิทธิ์กับ Terminal/Codex/Claude Code/Chrome ตามที่จำเป็น: Accessibility, Automation, Screen Recording
   - หลังให้สิทธิ์แล้วให้ลองใหม่

12. รายงานผลสุดท้ายแบบสั้น
   - path repo
   - URL backend/frontend
   - สถานะ Docker containers
   - สถานะ health/runtime/provider
   - provider ที่พร้อมใช้งาน
   - สิ่งที่ยังต้องให้ผู้ใช้ login หรือใส่ key เพิ่ม ถ้ามี
   - คำสั่ง restart แบบสั้น
   - ห้ามรายงาน secret จริง

ให้เริ่มทำทันทีจากการตรวจเครื่องและติดตั้ง full stack ให้จนพร้อมใช้งาน
```

---

## ผลลัพธ์ที่ควรได้

- Repo อยู่ที่ `~/Projects/ai-company`
- Repo ไม่อยู่ใน iCloud Drive, Desktop หรือ Documents ที่ sync กับ iCloud
- Docker containers สำหรับ Postgres, Ollama และ Letta ทำงานอยู่
- Ollama มี model `bge-m3`
- Backend เปิดที่ `http://127.0.0.1:8787`
- Frontend เปิดที่ `http://127.0.0.1:5173`
- หน้า ATRIUM เปิดใน browser ได้
- มี provider อย่างน้อย 1 ตัวที่พร้อมตอบงานจริง

## ถ้า agent ติด blocker

เรื่องที่ agent อาจทำแทนไม่ได้ทั้งหมด:

- ใส่ password ของ Mac
- เปิด Docker Desktop ครั้งแรก
- login Claude หรือ ChatGPT
- ใส่ API key
- กดอนุญาต macOS Privacy & Security
- ขอสิทธิ์เข้า private GitHub repo

ถ้าเกิดกรณีนี้ ให้ทำตามที่ agent บอก แล้วสั่งต่อว่า:

```text
ทำต่อจากจุดที่ค้าง ตรวจสอบ full stack ซ้ำ แล้วเปิด ATRIUM ให้พร้อมใช้งาน
```

---

# English Guide: Install ATRIUM Full Stack With One Prompt

This English section is for users who prefer English. The Thai section above is the primary guide. Both sections install the same full stack and use the same target folder.

Repository:

```text
https://github.com/Phonsadboy/ATRIUM-TH.git
```

## Quick Use

1. Open Codex or Claude Code on the Mac that will run ATRIUM
2. Copy the English prompt below
3. Let the agent install and verify the full stack
4. Follow any requests to log in, enter API keys, start Docker Desktop, or grant macOS permissions
5. Open `http://127.0.0.1:5173`

## Requirements

- One Mac, preferably Apple Silicon with at least 16GB RAM
- At least 20GB of free disk space
- Access to the GitHub repo `Phonsadboy/ATRIUM-TH`
- At least one usable Claude or ChatGPT account
- Optional API keys: OpenAI Platform API key or Anthropic API key

## Install Location

Use:

```text
~/Projects/ai-company
```

Do not install inside iCloud Drive. Avoid `Desktop` and `Documents` if iCloud sync is enabled for those folders. Databases, Docker volumes, `node_modules`, virtual environments, and runtime files should not live in synced folders.

## English Prompt For Codex / Claude Code

Copy this whole prompt:

```text
You are a coding agent installing ATRIUM / AI Company full stack on this Mac until it is ready for real use.

Repository:
https://github.com/Phonsadboy/ATRIUM-TH.git

Final goals:
- Repo path is ~/Projects/ai-company
- Do not place the repo in iCloud Drive
- Do not place the repo in Desktop or Documents if those folders are synced by iCloud
- Full stack is required from the start: Postgres/pgvector + Ollama + Letta + FastAPI backend + Vite frontend
- Backend runs at http://127.0.0.1:8787
- Frontend runs at http://127.0.0.1:5173
- Verify health, runtime, and provider status
- Open the web app for the user
- Do not stop at advice. Actually install and run everything unless blocked by a user-only action such as login, secret entry, password prompt, or macOS permission approval.

Safety rules:
- Never print secrets, API keys, OAuth tokens, or passwords in chat
- If secrets are needed, ask the user to enter them in system/.env, macOS Keychain, a provider login, or an OAuth flow
- If an existing server is running, inspect it before stopping anything
- If the repo has local changes, do not reset, overwrite, or delete them without asking
- If an installer requires a password or GUI confirmation, clearly tell the user what to do and wait

Install steps:

1. Check the Mac and install location
   - Check macOS, CPU, RAM, shell, and current directory
   - Make sure the target path is not under iCloud Drive, ~/Library/Mobile Documents, Desktop synced by iCloud, or Documents synced by iCloud
   - Use only this target path: ~/Projects/ai-company
   - Create ~/Projects if needed

2. Check and install basic tools
   - Check for git, brew, node, pnpm, uv, python, docker, docker compose, and Google Chrome
   - If Homebrew is missing, install it or ask the user to install it from https://brew.sh when password confirmation is required
   - Install missing tools when possible:
     brew install git node pnpm uv
     brew install --cask docker
     brew install --cask google-chrome
   - If Docker Desktop was just installed, open it and wait until Docker is ready

3. Prepare the repo
   - If the repo does not exist:
     git clone https://github.com/Phonsadboy/ATRIUM-TH.git ~/Projects/ai-company
   - If it already exists:
     cd ~/Projects/ai-company
     git remote -v
     git status --short --branch
   - If the remote is not https://github.com/Phonsadboy/ATRIUM-TH.git, stop and ask the user
   - If there are local changes, report them and ask before pulling or editing
   - If safe, run git pull

4. Prepare at least one real provider
   - Use at least one provider the user actually has:
     Claude Code account
     ChatGPT account OAuth
     OpenAI Platform API key
     Anthropic API key
   - If the claude command exists, run: claude auth status --json
   - If Claude Code is needed but not ready:
     npm install -g @anthropic-ai/claude-code
     then ask the user to run: claude setup-token
   - If the user wants ChatGPT account OAuth, run from the repo root:
     uv --project system run python ops/chatgpt_account_oauth_login.py
     then let the user finish login in the browser
   - If the user uses an OpenAI Platform API key or Anthropic API key, ask the user to enter it in system/.env. Do not print the secret in chat.

5. Create system/.env for full stack
   - Go to ~/Projects/ai-company/system
   - If system/.env does not exist, run: cp .env.example .env
   - Before setting concurrency, ask the user how many ATRIUM jobs they want to run at the same time and explain:
     ATRIUM_CHAT_REPLY_WORKER_CONCURRENCY is the number of department chat replies that may run concurrently
     ATRIUM_DEPARTMENT_WORKER_CONCURRENCY is the number of autonomous department work/review steps the engine may run concurrently per tick
     If unsure, use the default 5; a stronger Mac may reasonably use 10-20, but higher values consume more CPU, RAM, and provider quota/API capacity
   - Add or update these full-stack settings:
     ATRIUM_AGENT_BACKEND=letta
     ATRIUM_DATABASE_URL=postgresql+asyncpg://atrium:atrium@127.0.0.1:5432/atrium
     ATRIUM_DATA_DIR=./data
     ATRIUM_GRAPH_BACKEND=auto
     ATRIUM_HOST=127.0.0.1
     ATRIUM_PORT=8787
     ATRIUM_CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
     ATRIUM_OLLAMA_BASE_URL=http://127.0.0.1:11434
     ATRIUM_OLLAMA_EMBEDDING_MODEL=bge-m3
     ATRIUM_EMBEDDING_DIM=1024
     ATRIUM_LETTA_BASE_URL=http://127.0.0.1:8283
     ATRIUM_OBJECT_STORE_ENABLED=true
     CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
     ATRIUM_CHAT_REPLY_WORKER_CONCURRENCY=5
     ATRIUM_DEPARTMENT_WORKER_CONCURRENCY=5
   - If using OpenAI Platform API key, ask the user to enter:
     ATRIUM_OPENAI_API_KEY=...
     ATRIUM_OPENAI_BASE_URL=https://api.openai.com/v1
   - If using Anthropic API key, ask the user to enter:
     ATRIUM_ANTHROPIC_AUTH_TOKEN=...
     ATRIUM_ANTHROPIC_BASE_URL=https://api.anthropic.com
   - If Letta needs an Anthropic API key, make ANTHROPIC_API_KEY available to Docker Compose without printing it in chat

6. Install dependencies
   - Backend:
     cd ~/Projects/ai-company/system
     uv sync --extra live --extra postgres --extra graph
   - Frontend:
     cd ~/Projects/ai-company/ui
     pnpm install
   - Create or update ui/.env.local:
     VITE_ATRIUM_API_URL=http://127.0.0.1:8787

7. Start Docker full stack
   - From repo root:
     cd ~/Projects/ai-company
   - Check Docker: docker info
   - Start Postgres and Ollama: docker compose up -d postgres ollama
   - Start Letta: docker compose --profile v2 up -d letta
   - Pull embedding model: docker compose exec ollama ollama pull bge-m3
   - Verify containers: docker compose ps and docker compose exec ollama ollama list

8. Run database migrations
   - cd ~/Projects/ai-company/system
   - uv run --extra postgres alembic -c alembic.ini upgrade head
   - If migration fails, fix from the actual error

9. Start backend and frontend
   - Check ports with lsof -i :8787 and lsof -i :5173
   - If an old ATRIUM process owns the port, report before stopping it
   - Start backend:
     cd ~/Projects/ai-company/system
     uv run --extra live --extra postgres --extra graph python -m app
   - Start frontend:
     cd ~/Projects/ai-company/ui
     pnpm dev --host 127.0.0.1 --port 5173
   - Use separate terminal sessions or detached processes that keep running after the task ends

10. Verify readiness
   - Check:
     curl http://127.0.0.1:8787/health
     curl http://127.0.0.1:8787/api/runtime
     curl http://127.0.0.1:8787/api/provider-auth/status
   - Verify runtime is not degraded because of Postgres, Ollama, or Letta
   - Open http://127.0.0.1:5173
   - Ask the user to test Executive chat with:
     Summarize which parts of the system are ready right now.

11. macOS permissions
   - If browser or desktop automation does not work, ask the user to open System Settings > Privacy & Security
   - Grant permissions to Terminal/Codex/Claude Code/Chrome as needed: Accessibility, Automation, Screen Recording
   - Retry after permissions are granted

12. Final report
   - Repo path
   - Backend/frontend URLs
   - Docker container status
   - Health/runtime/provider status
   - Provider ready for use
   - Anything still requiring login or key entry
   - Short restart commands
   - Do not reveal secrets

Start now by checking this Mac and installing the full stack until it is ready.
```

---

## Restart Commands

Terminal 1:

```bash
cd ~/Projects/ai-company
docker compose up -d postgres ollama
docker compose --profile v2 up -d letta
```

Terminal 2:

```bash
cd ~/Projects/ai-company/system
uv run --extra live --extra postgres --extra graph python -m app
```

Terminal 3:

```bash
cd ~/Projects/ai-company/ui
pnpm dev --host 127.0.0.1 --port 5173
```

Open:

```text
http://127.0.0.1:5173
```
