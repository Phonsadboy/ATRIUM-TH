#!/usr/bin/env python3
"""ATRIUM local setup shortcut.

This module intentionally uses only the Python standard library so `./atrium
doctor` works immediately after a fresh clone.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


ROOT = Path(__file__).resolve().parents[1]
SYSTEM_DIR = ROOT / "system"
UI_DIR = ROOT / "ui"
LOG_DIR = SYSTEM_DIR / "logs"
SYSTEM_ENV = SYSTEM_DIR / ".env"
SYSTEM_ENV_EXAMPLE = SYSTEM_DIR / ".env.example"
UI_ENV = UI_DIR / ".env.local"
EXPECTED_REMOTE_SLUG = "Phonsadboy/ATRIUM-TH"
BACKEND_URL = "http://127.0.0.1:8787"
FRONTEND_URL = "http://127.0.0.1:5173"
BACKEND_SCREEN = "ai-company-backend"
UI_SCREEN = "ai-company-ui"
PORTS = {
    8787: "backend",
    5173: "frontend",
    5432: "postgres",
    8283: "letta",
    11434: "ollama",
}
SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH")
FULL_STACK_DEFAULTS = {
    "ATRIUM_AGENT_BACKEND": "letta",
    "ATRIUM_DATABASE_URL": "postgresql+asyncpg://atrium:atrium@127.0.0.1:5432/atrium",
    "ATRIUM_DATA_DIR": "./data",
    "ATRIUM_GRAPH_BACKEND": "auto",
    "ATRIUM_HOST": "127.0.0.1",
    "ATRIUM_PORT": "8787",
    "ATRIUM_CORS_ORIGINS": "http://localhost:5173,http://127.0.0.1:5173",
    "ATRIUM_OLLAMA_BASE_URL": "http://127.0.0.1:11434",
    "ATRIUM_OLLAMA_EMBEDDING_MODEL": "bge-m3",
    "ATRIUM_EMBEDDING_DIM": "1024",
    "ATRIUM_LETTA_BASE_URL": "http://127.0.0.1:8283",
    "ATRIUM_OBJECT_STORE_ENABLED": "true",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "ATRIUM_CHAT_REPLY_WORKER_CONCURRENCY": "5",
    "ATRIUM_DEPARTMENT_WORKER_CONCURRENCY": "5",
}
UI_DEFAULTS = {"VITE_ATRIUM_API_URL": BACKEND_URL}


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass
class EnvUpdate:
    action: str
    path: Path
    created: bool
    changed_keys: list[str]
    preserved_keys: list[str]


class StepFailure(RuntimeError):
    def __init__(self, message: str, *, next_step: str | None = None):
        super().__init__(message)
        self.next_step = next_step


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def print_header(title: str) -> None:
    print(f"\n== {title} ==")


def print_check(ok: bool, label: str, detail: str = "") -> None:
    status = "OK" if ok else "BLOCKED"
    suffix = f" - {detail}" if detail else ""
    print(f"[{status}] {label}{suffix}")


def print_info(label: str, detail: str = "") -> None:
    suffix = f" - {detail}" if detail else ""
    print(f"[INFO] {label}{suffix}")


def redact_value(key: str, value: str | None) -> str:
    if value is None or value == "":
        return "missing"
    upper = key.upper()
    if any(marker in upper for marker in SECRET_MARKERS):
        return "set"
    if "postgresql" in value and "@" in value:
        return re.sub(r":([^:@/]+)@", ":***@", value)
    return value


def redact_text(text: str) -> str:
    redacted: list[str] = []
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            redacted.append(f"{key}={redact_value(key.strip(), value.strip())}")
        else:
            redacted.append(line)
    return "\n".join(redacted)


def run(
    args: Sequence[str],
    *,
    cwd: Path = ROOT,
    timeout: int = 30,
    check: bool = False,
    env: dict[str, str] | None = None,
) -> CommandResult:
    try:
        completed = subprocess.run(
            list(args),
            cwd=str(cwd),
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        result = CommandResult(127, "", f"{args[0]} not found")
    except subprocess.TimeoutExpired as exc:
        result = CommandResult(124, exc.stdout or "", exc.stderr or f"timed out after {timeout}s")
    else:
        result = CommandResult(completed.returncode, completed.stdout, completed.stderr)
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise StepFailure(f"{' '.join(args)} failed: {detail[:1000]}")
    return result


def command_path(name: str) -> str | None:
    return shutil.which(name)


def docker_compose_cmd() -> list[str] | None:
    docker = command_path("docker")
    if not docker:
        return None
    compose = run(["docker", "compose", "version"], timeout=10)
    if compose.returncode == 0:
        return ["docker", "compose"]
    legacy = command_path("docker-compose")
    if legacy:
        return [legacy]
    return None


def ensure_repo_root() -> None:
    if not (ROOT / "docker-compose.yml").exists() or not SYSTEM_DIR.exists() or not UI_DIR.exists():
        raise StepFailure(f"{ROOT} does not look like an ATRIUM repo root")


def remote_ok() -> tuple[bool, str]:
    if not (ROOT / ".git").exists():
        return False, "not a git checkout"
    result = run(["git", "remote", "-v"], timeout=10)
    remotes = result.stdout.strip()
    if result.returncode != 0:
        return False, (result.stderr or "git remote failed").strip()
    normalized = remotes.replace(":", "/")
    return EXPECTED_REMOTE_SLUG in normalized, remotes or "no remotes configured"


def git_status_summary() -> str:
    result = run(["git", "status", "--short", "--branch"], timeout=10)
    if result.returncode != 0:
        return (result.stderr or "git status failed").strip()
    return result.stdout.strip() or "clean"


def is_i_cloud_risky(path: Path) -> tuple[bool, str]:
    expanded = path.expanduser().resolve()
    home = Path.home().resolve()
    risky_parts = ("Library/Mobile Documents", "iCloud Drive")
    raw = str(expanded)
    if any(part in raw for part in risky_parts):
        return True, f"{expanded} is under iCloud storage"
    for folder in ("Desktop", "Documents"):
        candidate = home / folder
        try:
            expanded.relative_to(candidate)
        except ValueError:
            continue
        mobile_docs = home / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / folder
        if mobile_docs.exists():
            return True, f"{expanded} is under {folder}, which may be iCloud-synced"
    return False, str(expanded)


def memory_gb() -> str:
    if platform.system() == "Darwin":
        result = run(["sysctl", "-n", "hw.memsize"], timeout=5)
        if result.returncode == 0:
            try:
                return f"{int(result.stdout.strip()) / 1024**3:.1f} GB"
            except ValueError:
                pass
    return "unknown"


def chrome_installed() -> bool:
    mac_path = Path("/Applications/Google Chrome.app")
    return mac_path.exists() or command_path("google-chrome") is not None or command_path("chromium") is not None


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def port_owner(port: int) -> str:
    result = run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"], timeout=5)
    if result.returncode != 0 or not result.stdout.strip():
        return "free"
    lines = result.stdout.strip().splitlines()
    return "; ".join(line.strip() for line in lines[1:4]) if len(lines) > 1 else lines[0]


def http_get_json(url: str, *, timeout: float = 3.0) -> tuple[bool, str, object | None]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        return False, str(exc), None
    except TimeoutError as exc:
        return False, str(exc), None
    try:
        return True, body, json.loads(body)
    except json.JSONDecodeError:
        return True, body, None


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def render_env_status(path: Path, keys: Iterable[str]) -> list[str]:
    values = parse_env_file(path)
    return [f"{key}={redact_value(key, values.get(key))}" for key in keys]


def merge_env_text(existing: str, defaults: dict[str, str]) -> tuple[str, list[str], list[str]]:
    lines = existing.splitlines()
    present: dict[str, int] = {}
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        present[key] = idx

    changed: list[str] = []
    preserved: list[str] = []
    for key, value in defaults.items():
        idx = present.get(key)
        if idx is None:
            changed.append(key)
            lines.append(f"{key}={value}")
            continue
        current = lines[idx].split("=", 1)[1].strip()
        if current:
            preserved.append(key)
            continue
        changed.append(key)
        lines[idx] = f"{key}={value}"

    text = "\n".join(lines).rstrip() + "\n"
    return text, changed, preserved


def update_env_file(
    path: Path,
    defaults: dict[str, str],
    *,
    template: Path | None = None,
    dry_run: bool = False,
) -> EnvUpdate:
    created = not path.exists()
    existing = ""
    if path.exists():
        existing = path.read_text(encoding="utf-8")
    elif template and template.exists():
        existing = template.read_text(encoding="utf-8")
    new_text, changed, preserved = merge_env_text(existing, defaults)
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_text, encoding="utf-8")
    return EnvUpdate("update-env", path, created, changed, preserved)


def run_or_plan(args: Sequence[str], *, cwd: Path = ROOT, dry_run: bool = False, timeout: int = 120) -> CommandResult:
    if dry_run:
        print(f"[DRY-RUN] {' '.join(args)}")
        return CommandResult(0, "", "")
    print(f"$ {' '.join(args)}")
    result = run(args, cwd=cwd, timeout=timeout)
    if result.stdout.strip():
        print(redact_text(result.stdout.strip()))
    if result.returncode != 0:
        detail = redact_text((result.stderr or result.stdout).strip())
        raise StepFailure(f"{' '.join(args)} failed", next_step=detail[:1200])
    return result


def install_missing_tools(dry_run: bool) -> None:
    required = ["git", "node", "pnpm", "uv"]
    missing = [tool for tool in required if not command_path(tool)]
    brew = command_path("brew")
    if missing:
        if not brew:
            raise StepFailure(
                f"missing tools: {', '.join(missing)}",
                next_step="Install Homebrew from https://brew.sh, then run ./atrium bootstrap --full again.",
            )
        run_or_plan(["brew", "install", *missing], dry_run=dry_run, timeout=900)
    if not command_path("docker"):
        if not brew:
            raise StepFailure(
                "Docker is missing",
                next_step="Install Docker Desktop, open it once, then run ./atrium bootstrap --full again.",
            )
        run_or_plan(["brew", "install", "--cask", "docker"], dry_run=dry_run, timeout=900)
        raise StepFailure(
            "Docker Desktop may need a GUI start after installation",
            next_step="Open Docker Desktop and wait until it says it is running, then run ./atrium bootstrap --full again.",
        )
    if not chrome_installed() and brew:
        run_or_plan(["brew", "install", "--cask", "google-chrome"], dry_run=dry_run, timeout=900)


def assert_docker_ready() -> None:
    if not command_path("docker"):
        raise StepFailure("Docker is not installed", next_step="Install Docker Desktop and run ./atrium bootstrap --full again.")
    result = run(["docker", "info"], timeout=15)
    if result.returncode != 0:
        raise StepFailure(
            "Docker is not running",
            next_step="Open Docker Desktop, wait until Docker is ready, then run ./atrium bootstrap --full again.",
        )


def compose(args: Sequence[str], *, dry_run: bool = False, timeout: int = 300) -> CommandResult:
    cmd = docker_compose_cmd()
    if not cmd:
        raise StepFailure("Docker Compose is unavailable", next_step="Install/update Docker Desktop, then run ./atrium doctor.")
    return run_or_plan([*cmd, *args], dry_run=dry_run, timeout=timeout)


def screen_sessions() -> str:
    if not command_path("screen"):
        return "screen not installed"
    result = run(["screen", "-ls"], timeout=10)
    output = (result.stdout + result.stderr).strip()
    return output or "no screen sessions"


def screen_session_exists(name: str) -> bool:
    output = screen_sessions()
    return f".{name}" in output or f"\t{name}" in output


def provider_status_from_env() -> list[str]:
    values = parse_env_file(SYSTEM_ENV)
    statuses: list[str] = []
    for key, label in (
        ("ATRIUM_OPENAI_API_KEY", "OpenAI Platform API key"),
        ("ATRIUM_ANTHROPIC_AUTH_TOKEN", "Anthropic API key"),
        ("ATRIUM_IMAGE_GENERATION_API_KEY", "Image generation API key"),
    ):
        statuses.append(f"{label}: {redact_value(key, values.get(key))}")
    claude = command_path("claude")
    if claude:
        result = run(["claude", "auth", "status", "--json"], timeout=8)
        if result.returncode == 0:
            statuses.append("Claude Code account: command available, auth status responded")
        else:
            statuses.append("Claude Code account: command available, auth status not ready")
    else:
        statuses.append("Claude Code account: claude command missing")
    return statuses


def command_doctor(_args: argparse.Namespace) -> int:
    ensure_repo_root()
    print_header("Machine")
    print_info("repo", str(ROOT))
    print_info("macOS", platform.platform())
    print_info("CPU", platform.machine() or "unknown")
    print_info("RAM", memory_gb())
    risky, detail = is_i_cloud_risky(ROOT)
    print_check(not risky, "install path", detail)

    print_header("Git")
    ok, detail = remote_ok()
    print_check(ok, "remote", detail.replace("\n", " | "))
    print_info("status", git_status_summary().replace("\n", " | "))

    print_header("Tools")
    for tool in ("git", "brew", "node", "pnpm", "uv", "python3", "docker"):
        found = command_path(tool)
        print_check(bool(found), tool, found or "missing")
    compose_cmd = docker_compose_cmd()
    print_check(bool(compose_cmd), "docker compose", " ".join(compose_cmd) if compose_cmd else "missing")
    print_check(chrome_installed(), "Google Chrome", "installed" if chrome_installed() else "missing")

    print_header("Ports")
    for port, label in PORTS.items():
        open_now = port_open(port)
        detail = port_owner(port) if open_now else "free"
        print_info(f"{label} :{port}", detail)

    print_header("Env")
    print_check(SYSTEM_ENV.exists(), rel(SYSTEM_ENV), "exists" if SYSTEM_ENV.exists() else "missing")
    for line in render_env_status(SYSTEM_ENV, FULL_STACK_DEFAULTS.keys()):
        print_info(line)
    print_check(UI_ENV.exists(), rel(UI_ENV), "exists" if UI_ENV.exists() else "missing")
    for line in render_env_status(UI_ENV, UI_DEFAULTS.keys()):
        print_info(line)

    print_header("Runtime")
    for path in ("/health", "/api/runtime", "/api/provider-auth/status"):
        ok, raw, payload = http_get_json(f"{BACKEND_URL}{path}", timeout=15.0 if path == "/api/runtime" else 3.0)
        if ok and isinstance(payload, dict):
            summary = f"ok={payload.get('ok', 'unknown')}"
            if path == "/api/runtime":
                summary += f", running={payload.get('running', 'unknown')}"
            print_check(True, path, summary)
        elif ok:
            print_check(True, path, raw[:120])
        else:
            print_check(False, path, raw)

    print_header("Provider")
    for line in provider_status_from_env():
        print_info(line)
    return 0


def command_bootstrap(args: argparse.Namespace) -> int:
    ensure_repo_root()
    if not args.full:
        raise StepFailure("bootstrap currently supports only --full", next_step="./atrium bootstrap --full")

    risky, detail = is_i_cloud_risky(ROOT)
    if risky:
        raise StepFailure("install path is not safe for local runtime files", next_step=detail)
    ok, detail = remote_ok()
    if not ok:
        raise StepFailure(
            "repo remote does not match ATRIUM-TH",
            next_step=f"Expected a remote containing {EXPECTED_REMOTE_SLUG}. Current remote: {detail}",
        )

    print_header("Prepare Env")
    system_update = update_env_file(SYSTEM_ENV, FULL_STACK_DEFAULTS, template=SYSTEM_ENV_EXAMPLE, dry_run=args.dry_run)
    ui_update = update_env_file(UI_ENV, UI_DEFAULTS, dry_run=args.dry_run)
    for update in (system_update, ui_update):
        created = "created" if update.created else "preserved"
        print_info(rel(update.path), f"{created}; filled {len(update.changed_keys)} keys; preserved {len(update.preserved_keys)} keys")
        if update.changed_keys:
            print_info("filled", ", ".join(update.changed_keys))

    print_header("Install Tools")
    install_missing_tools(args.dry_run)

    print_header("Install Dependencies")
    run_or_plan(["uv", "sync", "--extra", "live", "--extra", "postgres", "--extra", "graph"], cwd=SYSTEM_DIR, dry_run=args.dry_run, timeout=1200)
    run_or_plan(["pnpm", "install"], cwd=UI_DIR, dry_run=args.dry_run, timeout=1200)

    print_header("Docker Stack")
    if not args.dry_run:
        assert_docker_ready()
    else:
        print("[DRY-RUN] docker info")
    compose(["up", "-d", "postgres", "ollama"], dry_run=args.dry_run, timeout=600)
    compose(["--profile", "v2", "up", "-d", "letta"], dry_run=args.dry_run, timeout=600)
    compose(["exec", "ollama", "ollama", "pull", "bge-m3"], dry_run=args.dry_run, timeout=1200)

    print_header("Database")
    run_or_plan(["uv", "run", "--extra", "postgres", "alembic", "-c", "alembic.ini", "upgrade", "head"], cwd=SYSTEM_DIR, dry_run=args.dry_run, timeout=600)
    print("\nBootstrap complete. Run ./atrium start, then ./atrium status.")
    return 0


def assert_port_available_for_start(port: int, label: str) -> None:
    if not port_open(port):
        return
    owner = port_owner(port)
    raise StepFailure(
        f"{label} port {port} is already in use",
        next_step=f"Inspect this listener before starting ATRIUM:\n{owner}",
    )


def command_start(args: argparse.Namespace) -> int:
    ensure_repo_root()
    if not command_path("screen"):
        raise StepFailure("screen is unavailable", next_step="Install screen or start backend/frontend in separate terminals.")
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    if not args.force:
        assert_port_available_for_start(8787, "backend")
        assert_port_available_for_start(5173, "frontend")

    print_header("Start Docker")
    if command_path("docker") and docker_compose_cmd():
        if run(["docker", "info"], timeout=10).returncode == 0:
            compose(["up", "-d", "postgres", "ollama"], timeout=300)
            compose(["--profile", "v2", "up", "-d", "letta"], timeout=300)
        else:
            print_check(False, "Docker", "not running; open Docker Desktop if full stack services are missing")

    print_header("Start Services")
    backend_log = LOG_DIR / "backend.log"
    ui_log = LOG_DIR / "ui.log"
    if screen_session_exists(BACKEND_SCREEN):
        print_info("backend", f"screen session already exists: {BACKEND_SCREEN}")
    else:
        backend_cmd = (
            f"cd {shell_quote(str(SYSTEM_DIR))} && "
            f"exec uv run --extra live --extra postgres --extra graph python -m app >>{shell_quote(str(backend_log))} 2>&1"
        )
        run_or_plan(["screen", "-dmS", BACKEND_SCREEN, "zsh", "-lc", backend_cmd], timeout=20)
        print_info("backend", f"started in screen {BACKEND_SCREEN}; log {rel(backend_log)}")

    if screen_session_exists(UI_SCREEN):
        print_info("frontend", f"screen session already exists: {UI_SCREEN}")
    else:
        ui_cmd = (
            f"cd {shell_quote(str(UI_DIR))} && "
            f"exec pnpm dev --host 127.0.0.1 --port 5173 >>{shell_quote(str(ui_log))} 2>&1"
        )
        run_or_plan(["screen", "-dmS", UI_SCREEN, "zsh", "-lc", ui_cmd], timeout=20)
        print_info("frontend", f"started in screen {UI_SCREEN}; log {rel(ui_log)}")

    wait_for_urls(args.wait_seconds)
    print(f"\nFrontend: {FRONTEND_URL}")
    print(f"Backend:  {BACKEND_URL}")
    print("Run ./atrium status for readiness details.")
    return 0


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def wait_for_urls(seconds: int) -> None:
    if seconds <= 0:
        return
    deadline = time.time() + seconds
    while time.time() < deadline:
        health, _, _ = http_get_json(f"{BACKEND_URL}/health", timeout=1.5)
        frontend = port_open(5173)
        if health and frontend:
            return
        time.sleep(1)


def summarize_runtime_payload(payload: object) -> str:
    if not isinstance(payload, dict):
        return "non-json"
    parts = [f"ok={payload.get('ok', 'unknown')}"]
    if "running" in payload:
        parts.append(f"running={payload.get('running')}")
    agent = payload.get("agentRuntime")
    if isinstance(agent, dict):
        parts.append(f"agentRuntime.ok={agent.get('ok', 'unknown')}")
        parts.append(f"backend={agent.get('configuredBackend', agent.get('backend', 'unknown'))}")
    memory = payload.get("memory")
    if isinstance(memory, dict):
        parts.append(f"memory.ok={memory.get('ok', 'unknown')}")
    graph = payload.get("graph")
    if isinstance(graph, dict):
        parts.append(f"graph={graph.get('backend', graph.get('status', 'unknown'))}")
    return ", ".join(parts)


def command_status(_args: argparse.Namespace) -> int:
    ensure_repo_root()
    print_header("Processes")
    print_info("screen", screen_sessions().replace("\n", " | "))
    for port, label in PORTS.items():
        print_info(f"{label} :{port}", port_owner(port) if port_open(port) else "free")

    print_header("Docker")
    if command_path("docker") and docker_compose_cmd():
        if run(["docker", "info"], timeout=10).returncode == 0:
            cmd = docker_compose_cmd()
            assert cmd is not None
            result = run([*cmd, "ps"], timeout=30)
            if result.stdout.strip():
                print(redact_text(result.stdout.strip()))
        else:
            print_check(False, "Docker", "not running")
    else:
        print_check(False, "Docker Compose", "missing")

    print_header("HTTP")
    for path in ("/health", "/api/runtime", "/api/provider-auth/status"):
        ok, raw, payload = http_get_json(f"{BACKEND_URL}{path}", timeout=15.0 if path == "/api/runtime" else 3.0)
        if ok and path == "/api/runtime":
            print_check(True, path, summarize_runtime_payload(payload))
        elif ok and isinstance(payload, dict):
            print_check(True, path, f"ok={payload.get('ok', 'unknown')}")
        elif ok:
            print_check(True, path, raw[:120])
        else:
            print_check(False, path, raw)

    print_header("Provider")
    for line in provider_status_from_env():
        print_info(line)

    print_header("URLs")
    print_info("frontend", FRONTEND_URL)
    print_info("backend", BACKEND_URL)
    return 0


def command_stop(args: argparse.Namespace) -> int:
    ensure_repo_root()
    if not command_path("screen"):
        print_check(False, "screen", "missing")
        return 1
    print_header("Stop Screen Sessions")
    for name in (BACKEND_SCREEN, UI_SCREEN):
        if screen_session_exists(name):
            run_or_plan(["screen", "-S", name, "-X", "quit"], timeout=10)
            print_info(name, "stopped")
        else:
            print_info(name, "not running")
    if args.launchd:
        script = ROOT / "ops" / "launchd" / "atrium-launchd.sh"
        if script.exists():
            run_or_plan([str(script), "uninstall"], timeout=30)
    return 0


def command_logs(args: argparse.Namespace) -> int:
    ensure_repo_root()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    files = {
        "backend": LOG_DIR / "backend.log",
        "ui": LOG_DIR / "ui.log",
    }
    selected = files if args.service == "all" else {args.service: files[args.service]}
    for label, path in selected.items():
        print_header(f"{label} log: {rel(path)}")
        if not path.exists():
            print("missing")
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-args.lines :]
        print(redact_text("\n".join(lines)))
    return 0


def report_lines() -> list[str]:
    lines = [
        "# ATRIUM support report",
        f"repo={ROOT}",
        f"platform={platform.platform()}",
        f"machine={platform.machine()}",
        f"ram={memory_gb()}",
        f"git_status={git_status_summary().replace(chr(10), ' | ')}",
    ]
    ok, remote = remote_ok()
    lines.append(f"remote_ok={ok}")
    lines.append(f"remote={remote.replace(chr(10), ' | ')}")
    risky, detail = is_i_cloud_risky(ROOT)
    lines.append(f"path_risky={risky}")
    lines.append(f"path_detail={detail}")
    for tool in ("git", "brew", "node", "pnpm", "uv", "python3", "docker", "screen"):
        lines.append(f"tool.{tool}={'present' if command_path(tool) else 'missing'}")
    for port, label in PORTS.items():
        lines.append(f"port.{label}.{port}={port_owner(port) if port_open(port) else 'free'}")
    for path in ("/health", "/api/runtime", "/api/provider-auth/status"):
        ok, raw, payload = http_get_json(f"{BACKEND_URL}{path}", timeout=15.0 if path == "/api/runtime" else 3.0)
        if ok and path == "/api/runtime":
            lines.append(f"http.{path}={summarize_runtime_payload(payload)}")
        elif ok and isinstance(payload, dict):
            lines.append(f"http.{path}=ok={payload.get('ok', 'unknown')}")
        else:
            lines.append(f"http.{path}={raw[:180]}")
    lines.append("system.env:")
    lines.extend(render_env_status(SYSTEM_ENV, FULL_STACK_DEFAULTS.keys()))
    lines.append("ui.env:")
    lines.extend(render_env_status(UI_ENV, UI_DEFAULTS.keys()))
    lines.append("providers:")
    lines.extend(provider_status_from_env())
    return lines


def command_report(args: argparse.Namespace) -> int:
    ensure_repo_root()
    text = redact_text("\n".join(report_lines()) + "\n")
    if args.output:
        output = Path(args.output).expanduser()
        output.write_text(text, encoding="utf-8")
        print(f"wrote {output}")
    else:
        print(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ATRIUM local full-stack setup shortcut")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="check local setup without changing files").set_defaults(func=command_doctor)

    bootstrap = sub.add_parser("bootstrap", help="prepare the full local stack")
    bootstrap.add_argument("--full", action="store_true", help="prepare Postgres/Ollama/Letta/backend/frontend")
    bootstrap.add_argument("--dry-run", action="store_true", help="print planned actions without changing files or services")
    bootstrap.set_defaults(func=command_bootstrap)

    start = sub.add_parser("start", help="start backend and frontend in detached screen sessions")
    start.add_argument("--force", action="store_true", help="skip port availability guard")
    start.add_argument("--wait-seconds", type=int, default=20, help="wait for backend/frontend readiness after starting")
    start.set_defaults(func=command_start)

    sub.add_parser("status", help="show process, Docker, runtime, and provider status").set_defaults(func=command_status)

    stop = sub.add_parser("stop", help="stop ATRIUM-owned screen sessions")
    stop.add_argument("--launchd", action="store_true", help="also uninstall the ATRIUM LaunchAgent")
    stop.set_defaults(func=command_stop)

    logs = sub.add_parser("logs", help="show recent backend/UI logs")
    logs.add_argument("service", nargs="?", choices=("backend", "ui", "all"), default="all")
    logs.add_argument("-n", "--lines", type=int, default=80)
    logs.set_defaults(func=command_logs)

    report = sub.add_parser("report", help="print a redacted support report")
    report.add_argument("-o", "--output", help="write report to a file instead of stdout")
    report.set_defaults(func=command_report)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except StepFailure as exc:
        print(f"\n[BLOCKED] {exc}", file=sys.stderr)
        if exc.next_step:
            print("\nNext step:", file=sys.stderr)
            print(redact_text(exc.next_step), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
