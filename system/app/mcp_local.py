"""Local MCP fallback used when no external MCP gateway is configured.

The real external gateway remains the preferred integration path. This module
keeps `mcp.call` executable on a standalone local host so the chat/tool surface
is not blocked by configuration alone; tools return structured capability/status
results and use local CLIs or synced folders when available.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

KNOWN_LOCAL_MCP_SERVERS: dict[str, dict[str, Any]] = {
    "github": {
        "capabilities": ["repositories", "issues", "pull_requests", "actions", "local_gh_cli"],
        "tools": ["status", "list_tools", "repo.view", "issues.list", "pull_requests.list", "actions.runs"],
    },
    "email": {
        "capabilities": ["mail_app_status", "draft_guidance"],
        "tools": ["status", "list_tools", "draft.guidance"],
    },
    "calendar": {
        "capabilities": ["calendar_app_status", "event_guidance"],
        "tools": ["status", "list_tools", "event.guidance"],
    },
    "notion": {
        "capabilities": ["notion_app_status", "workspace_guidance"],
        "tools": ["status", "list_tools", "workspace.guidance"],
    },
    "drive": {
        "capabilities": ["local_drive_status", "local_file_search"],
        "tools": ["status", "list_tools", "files.search"],
    },
}

KNOWN_EXECUTABLE_PATHS = {
    "gh": (
        "/opt/homebrew/bin/gh",
        "/usr/local/bin/gh",
        "C:/Program Files/GitHub CLI/gh.exe",
        "C:/Program Files (x86)/GitHub CLI/gh.exe",
    ),
}


def clip_text(text: str, limit: int = 60_000) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def mcp_gateway_endpoint(gateway_url: str | None) -> str:
    gateway = str(gateway_url or "").strip()
    if not gateway:
        return ""
    gateway = gateway.rstrip("/")
    return gateway if gateway.endswith("/call") else f"{gateway}/call"


def _keychain_token(service: str, account: str) -> tuple[str, dict[str, Any]]:
    status = {
        "keychainCliAvailable": bool(shutil.which("security")),
        "keychainServiceConfigured": bool(service),
        "keychainAccount": account,
        "keychainReadable": False,
    }
    if not service:
        return "", status
    security = shutil.which("security")
    if not security:
        return "", {**status, "error": "security CLI unavailable"}
    try:
        completed = subprocess.run(
            [security, "find-generic-password", "-s", service, "-a", account, "-w"],
            text=True,
            capture_output=True,
            timeout=3.0,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "", {**status, "error": "keychain lookup timed out"}
    token = completed.stdout.strip() if completed.returncode == 0 else ""
    if token:
        return token, {**status, "keychainReadable": True}
    error = (completed.stderr or "").strip()
    return "", {**status, "error": clip_text(error, 500) if error else "keychain item not found"}


def mcp_gateway_token_value(settings: Any) -> str:
    direct = str(getattr(settings, "mcp_gateway_token", "") or "").strip()
    if direct:
        return direct
    service = str(getattr(settings, "mcp_gateway_token_keychain_service", "") or "").strip()
    account = str(getattr(settings, "mcp_gateway_token_keychain_account", "") or "atrium").strip() or "atrium"
    token, _status = _keychain_token(service, account)
    return token


def mcp_gateway_token_status(settings: Any) -> dict[str, Any]:
    direct = str(getattr(settings, "mcp_gateway_token", "") or "").strip()
    service = str(getattr(settings, "mcp_gateway_token_keychain_service", "") or "").strip()
    account = str(getattr(settings, "mcp_gateway_token_keychain_account", "") or "atrium").strip() or "atrium"
    if direct:
        return {
            "configured": True,
            "source": "env",
            "envConfigured": True,
            "keychainServiceConfigured": bool(service),
            "keychainAccount": account,
            "secretRedacted": True,
        }
    token, keychain = _keychain_token(service, account)
    return {
        "configured": bool(token),
        "source": "keychain" if token else "missing",
        "envConfigured": False,
        **keychain,
        "secretRedacted": True,
    }


def mcp_enabled_servers(raw: str | None) -> set[str]:
    return {item.strip().lower() for item in str(raw or "").replace(";", ",").split(",") if item.strip()}


def mcp_server_enabled(server: str, raw: str | None) -> bool:
    enabled = mcp_enabled_servers(raw)
    return not enabled or "*" in enabled or server.strip().lower() in enabled


def mcp_unrestricted_policy(raw: str | None, *, known_servers: dict[str, Any] | None = None) -> dict[str, Any]:
    configured = mcp_enabled_servers(raw)
    visible_servers = sorted(server for server in configured if server != "*")
    return {
        "mode": "unrestricted",
        "badge": "unrestricted_mcp",
        "allowlistEnforced": False,
        "denyUnknownServers": False,
        "configuredServers": visible_servers,
        "configuredWildcard": "*" in configured,
        "configuredServersPurpose": "status_visibility_only_not_a_deny_gate",
        "auditRequired": True,
        "localFallbackServers": sorted((known_servers or KNOWN_LOCAL_MCP_SERVERS).keys()),
    }


def resolve_local_executable(name: str) -> str | None:
    resolved = shutil.which(name)
    if resolved:
        return resolved
    if sys.platform == "win32" and not name.lower().endswith(".exe"):
        resolved_exe = shutil.which(f"{name}.exe")
        if resolved_exe:
            return resolved_exe
    for candidate in KNOWN_EXECUTABLE_PATHS.get(name, ()):
        if Path(candidate).exists():
            return candidate
    return None


def mcp_runtime_block_reason(args: dict[str, Any], *, gateway_url: str | None, enabled_servers: str | None) -> str | None:
    server = str(args.get("server") or "").strip()
    if not server:
        return "mcp.call requires server"
    return None


def _run(command: list[str], *, cwd: Path | None = None, timeout: float = 10.0) -> dict[str, Any]:
    try:
        completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "cwd": str(cwd) if cwd else None,
            "returnCode": None,
            "timeout": True,
            "stdout": clip_text(exc.stdout or ""),
            "stderr": clip_text(exc.stderr or ""),
        }
    return {
        "command": command,
        "cwd": str(cwd) if cwd else None,
        "returnCode": completed.returncode,
        "stdout": clip_text(completed.stdout),
        "stderr": clip_text(completed.stderr),
    }


def _json_cli(command: list[str], *, cwd: Path | None = None, timeout: float = 15.0) -> dict[str, Any]:
    result = _run(command, cwd=cwd, timeout=timeout)
    parsed: Any = None
    if result.get("stdout"):
        try:
            parsed = json.loads(str(result["stdout"]))
        except json.JSONDecodeError:
            parsed = None
    return {**result, "json": parsed}


def _repo_arg(arguments: dict[str, Any]) -> list[str]:
    repo = str(arguments.get("repo") or arguments.get("repository") or "").strip()
    return ["--repo", repo] if repo else []


def _github_call(tool: str, arguments: dict[str, Any], *, cwd: Path | None) -> dict[str, Any]:
    gh = resolve_local_executable("gh")
    base = {
        "server": "github",
        "localGateway": True,
        "ghInstalled": bool(gh),
        "ghPath": gh,
    }
    if tool in {"status", "github.status"}:
        auth = _run([gh, "auth", "status"], cwd=cwd, timeout=10.0) if gh else None
        return {
            **base,
            "ok": bool(gh),
            "authenticated": bool(auth and auth.get("returnCode") == 0),
            "auth": auth,
        }
    if not gh:
        return {**base, "ok": False, "error": "GitHub CLI `gh` is not installed"}
    if tool in {"repo.view", "repositories", "repository.view"}:
        command = [
            gh,
            "repo",
            "view",
            *(str(arguments.get("repo") or arguments.get("repository")).split() if arguments.get("repo") or arguments.get("repository") else []),
            "--json",
            "nameWithOwner,description,url,defaultBranchRef,isPrivate",
        ]
        return {**base, "ok": True, "result": _json_cli(command, cwd=cwd)}
    if tool in {"issues.list", "issues"}:
        limit = str(max(1, min(int(arguments.get("limit") or 20), 100)))
        command = [gh, "issue", "list", *_repo_arg(arguments), "--limit", limit, "--json", "number,title,state,url,updatedAt"]
        return {**base, "ok": True, "result": _json_cli(command, cwd=cwd)}
    if tool in {"pull_requests.list", "pulls.list", "prs.list", "pull_requests"}:
        limit = str(max(1, min(int(arguments.get("limit") or 20), 100)))
        command = [gh, "pr", "list", *_repo_arg(arguments), "--limit", limit, "--json", "number,title,state,url,updatedAt,isDraft"]
        return {**base, "ok": True, "result": _json_cli(command, cwd=cwd)}
    if tool in {"actions.runs", "runs.list", "actions"}:
        limit = str(max(1, min(int(arguments.get("limit") or 20), 100)))
        command = [gh, "run", "list", *_repo_arg(arguments), "--limit", limit, "--json", "databaseId,status,conclusion,workflowName,displayTitle,url,createdAt"]
        return {**base, "ok": True, "result": _json_cli(command, cwd=cwd)}
    return _unsupported_tool("github", tool)


def _windows_start_menu_dirs() -> list[Path]:
    dirs: list[Path] = []
    program_data = os.environ.get("ProgramData")
    app_data = os.environ.get("APPDATA")
    if program_data:
        dirs.append(Path(program_data) / "Microsoft/Windows/Start Menu/Programs")
    if app_data:
        dirs.append(Path(app_data) / "Microsoft/Windows/Start Menu/Programs")
    return dirs


def _windows_app_status(app_names: list[str]) -> dict[str, Any]:
    needles = [name.strip().lower() for name in app_names if name.strip()]
    matches: list[dict[str, str]] = []
    for base in _windows_start_menu_dirs():
        if not base.exists():
            continue
        for shortcut in sorted(base.rglob("*.lnk")):
            haystack = f"{shortcut.stem} {shortcut}".lower()
            if any(needle in haystack for needle in needles):
                matches.append({"name": shortcut.stem, "path": str(shortcut), "kind": "shortcut"})
                if len(matches) >= 10:
                    break
        if len(matches) >= 10:
            break
    return {
        "installed": bool(matches),
        "path": matches[0]["path"] if matches else None,
        "matches": matches,
        "platform": sys.platform,
    }


def _app_status(app_name: str) -> dict[str, Any]:
    if sys.platform == "win32":
        aliases = {
            "Mail": ["Mail", "Outlook"],
            "Calendar": ["Calendar", "Outlook"],
            "Notion": ["Notion"],
        }
        return _windows_app_status(aliases.get(app_name, [app_name]))
    app_path = Path("/Applications") / f"{app_name}.app"
    return {"installed": app_path.exists(), "path": str(app_path) if app_path.exists() else None, "platform": sys.platform}


def _drive_roots(arguments: dict[str, Any]) -> list[Path]:
    raw_root = arguments.get("root") or arguments.get("path")
    if raw_root:
        root = Path(str(raw_root)).expanduser()
        return [root] if root.exists() and root.is_dir() else []
    roots = []
    if sys.platform == "win32":
        for env_name in ("OneDriveCommercial", "OneDriveConsumer", "OneDrive"):
            raw = os.environ.get(env_name)
            if raw:
                path = Path(raw)
                if path.exists() and path.is_dir():
                    roots.append(path)
        user_profile = Path(os.environ.get("USERPROFILE") or str(Path.home()))
        for name in ("Google Drive", "My Drive", "OneDrive"):
            path = user_profile / name
            if path.exists() and path.is_dir() and path not in roots:
                roots.append(path)
        return roots
    cloud = Path.home() / "Library" / "CloudStorage"
    if cloud.exists():
        roots.extend(path for path in cloud.iterdir() if path.is_dir() and path.name.lower().startswith("googledrive"))
    legacy = Path.home() / "Google Drive"
    if legacy.exists() and legacy.is_dir():
        roots.append(legacy)
    return roots


def _drive_call(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    roots = _drive_roots(arguments)
    base = {"server": "drive", "localGateway": True, "roots": [str(root) for root in roots]}
    if tool in {"status", "drive.status"}:
        return {**base, "ok": True, "localSyncAvailable": bool(roots)}
    if tool in {"files.search", "search"}:
        query = str(arguments.get("query") or "").lower()
        limit = max(1, min(int(arguments.get("limit") or 20), 100))
        matches: list[dict[str, Any]] = []
        scanned = 0
        for root in roots:
            for path in root.rglob("*"):
                scanned += 1
                if scanned > 10_000 or len(matches) >= limit:
                    break
                if not query or query in path.name.lower():
                    matches.append({
                        "name": path.name,
                        "path": str(path),
                        "type": "dir" if path.is_dir() else "file",
                        "bytes": path.stat().st_size if path.is_file() else None,
                    })
            if scanned > 10_000 or len(matches) >= limit:
                break
        return {**base, "ok": True, "query": query, "matches": matches, "scanned": scanned}
    return _unsupported_tool("drive", tool)


def _guidance_call(server: str, tool: str) -> dict[str, Any]:
    app = {"email": "Mail", "calendar": "Calendar", "notion": "Notion"}.get(server)
    status = _app_status(app) if app else {}
    if tool in {"status", f"{server}.status"}:
        return {"server": server, "localGateway": True, "ok": True, "app": status}
    if tool.endswith(".guidance"):
        return {
            "server": server,
            "localGateway": True,
            "ok": True,
            "app": status,
            "message": f"No account credential is embedded in ATRIUM. Use browser/desktop tools or configure an external {server} MCP server for write operations.",
        }
    return _unsupported_tool(server, tool)


def _unsupported_tool(server: str, tool: str) -> dict[str, Any]:
    info = KNOWN_LOCAL_MCP_SERVERS.get(server, {})
    return {
        "server": server,
        "localGateway": True,
        "ok": False,
        "error": f"unsupported local MCP tool: {tool}",
        "availableTools": info.get("tools", ["status", "list_tools"]),
    }


def execute_local_mcp_call(server: str, tool: str, arguments: dict[str, Any] | None = None, *, cwd: Path | None = None) -> dict[str, Any]:
    server_id = server.strip().lower()
    tool_name = tool.strip()
    args = arguments if isinstance(arguments, dict) else {}
    info = KNOWN_LOCAL_MCP_SERVERS.get(server_id)
    if tool_name in {"list_tools", "tools"}:
        return {
            "server": server_id,
            "tool": tool_name,
            "status": 200,
            "contentType": "application/json",
            "response": {
                "ok": True,
                "localGateway": True,
                "tools": (info or {}).get("tools", ["status", "list_tools"]),
                "capabilities": (info or {}).get("capabilities", []),
            },
        }
    if tool_name in {"capabilities", "capability"}:
        return {
            "server": server_id,
            "tool": tool_name,
            "status": 200,
            "contentType": "application/json",
            "response": {
                "ok": True,
                "localGateway": True,
                "capabilities": (info or {}).get("capabilities", []),
                "known": bool(info),
            },
        }
    if server_id == "github":
        response = _github_call(tool_name, args, cwd=cwd)
    elif server_id == "drive":
        response = _drive_call(tool_name, args)
    elif server_id in {"email", "calendar", "notion"}:
        response = _guidance_call(server_id, tool_name)
    elif tool_name in {"status", f"{server_id}.status"}:
        response = {"server": server_id, "localGateway": True, "ok": True, "known": False}
    else:
        response = _unsupported_tool(server_id, tool_name)
    return {
        "server": server_id,
        "tool": tool_name,
        "status": 200,
        "contentType": "application/json",
        "response": response,
    }
