"""Claude account provider through the local Claude Code CLI.

Anthropic's Claude account OAuth is scoped to Claude Code, so this provider uses
`claude -p` as the account/subscription runtime instead of treating the OAuth
token as an Anthropic Messages API credential.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from .base import LLMMessage, LLMResult, LLMStreamEvent, LLMToolCall

_AUTH_STATUS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_AUTH_STATUS_LOCK = threading.Lock()
_AUTH_STATUS_TTL_S = 30.0
_AUTH_STATUS_FAILURE_TTL_S = 2.0
_AUTH_STATUS_TIMEOUT_S = 15.0
_AUTH_STATUS_STALE_GRACE_S = 5 * 60.0
_IMAGE_DATA_URL_RE = re.compile(r"^data:(image/[A-Za-z0-9.+-]+);base64,(.+)$", re.DOTALL)
_IMAGE_EXTENSIONS = {
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


def _claude_code_probe_status_from_cache(
    *,
    base: dict[str, Any],
    cached: tuple[float, dict[str, Any]] | None,
    now: float,
    probe_status: str,
    probe_detail: str | None = None,
) -> dict[str, Any]:
    if cached:
        cached_at, cached_status = cached
        last_fresh_at = cached_status.get("lastFreshAt")
        if not isinstance(last_fresh_at, (int, float)):
            last_fresh_at = cached_at
        stale_age_s = max(0.0, now - float(last_fresh_at))
        if stale_age_s <= _AUTH_STATUS_STALE_GRACE_S and isinstance(cached_status.get("ready"), bool):
            state = "ready_stale" if cached_status.get("ready") else "not_logged_in_stale"
            status = {
                **cached_status,
                "fresh": False,
                "stale": True,
                "state": state,
                "status": f"{state}:{probe_status}",
                "probeFailed": True,
                "probeStatus": probe_status,
                "checkedAt": now,
                "lastFreshAt": float(last_fresh_at),
                "staleAgeS": round(stale_age_s, 3),
            }
            if probe_detail:
                status["probeDetail"] = probe_detail[:400]
            return status
    status = {
        **base,
        "ready": None,
        "loggedIn": None,
        "fresh": False,
        "stale": False,
        "state": "unknown",
        "status": probe_status,
        "probeFailed": True,
        "probeStatus": probe_status,
        "checkedAt": now,
    }
    if probe_detail:
        status["probeDetail"] = probe_detail[:400]
    return status


def claude_code_auth_status(command: str = "claude", timeout_s: float = _AUTH_STATUS_TIMEOUT_S) -> dict[str, Any]:
    resolved = shutil.which(command) if "/" not in command else command if Path(command).exists() else None
    base = {
        "ready": False,
        "command": command,
        "installed": bool(resolved),
        "loggedIn": False,
        "authMethod": None,
        "apiProvider": None,
        "subscriptionType": None,
        "email": None,
        "status": "missing" if not resolved else "unknown",
        "state": "missing" if not resolved else "unknown",
        "fresh": False,
        "stale": False,
        "probeFailed": False,
    }
    if not resolved:
        return base
    with _AUTH_STATUS_LOCK:
        cached = _AUTH_STATUS_CACHE.get(resolved)
        now = time.time()
        if cached:
            cached_at, cached_status = cached
            cache_ttl = (
                _AUTH_STATUS_FAILURE_TTL_S
                if cached_status.get("probeFailed") is True and not isinstance(cached_status.get("ready"), bool)
                else _AUTH_STATUS_TTL_S
            )
            if now - cached_at < cache_ttl:
                return cached_status
        try:
            proc = subprocess.run(
                [resolved, "auth", "status", "--json"],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=max(float(timeout_s or _AUTH_STATUS_TIMEOUT_S), 1.0),
            )
        except Exception as exc:
            status = _claude_code_probe_status_from_cache(
                base=base,
                cached=cached,
                now=now,
                probe_status=f"probe_failed:{type(exc).__name__}",
                probe_detail=str(exc),
            )
            _AUTH_STATUS_CACHE[resolved] = (now, status)
            return status
        return _parse_and_cache_auth_status(resolved=resolved, base=base, cached=cached, now=now, proc=proc)


def _parse_and_cache_auth_status(
    *,
    resolved: str,
    base: dict[str, Any],
    cached: tuple[float, dict[str, Any]] | None,
    now: float,
    proc: subprocess.CompletedProcess[str],
) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    with contextlib.suppress(Exception):
        value = json.loads(proc.stdout or "{}")
        if isinstance(value, dict):
            parsed = value
    if not isinstance(parsed.get("loggedIn"), bool):
        probe_detail = (proc.stderr or proc.stdout or f"exit {proc.returncode}")[:400]
        status = _claude_code_probe_status_from_cache(
            base=base,
            cached=cached,
            now=now,
            probe_status="probe_unknown:missing_loggedIn",
            probe_detail=probe_detail,
        )
        _AUTH_STATUS_CACHE[resolved] = (now, status)
        return status
    logged_in = bool(parsed.get("loggedIn"))
    ready = logged_in
    status = {
        **base,
        "ready": ready,
        "loggedIn": logged_in,
        "authMethod": parsed.get("authMethod"),
        "apiProvider": parsed.get("apiProvider"),
        "subscriptionType": parsed.get("subscriptionType"),
        "email": parsed.get("email"),
        "status": "ready" if ready else (proc.stderr or proc.stdout or "not_logged_in")[:400],
        "state": "ready" if ready else "not_logged_in",
        "fresh": True,
        "stale": False,
        "probeFailed": False,
        "checkedAt": now,
        "lastFreshAt": now,
        "returnCode": proc.returncode,
    }
    _AUTH_STATUS_CACHE[resolved] = (now, status)
    return status


def start_claude_code_login(command: str = "claude") -> dict[str, Any]:
    status = claude_code_auth_status(command)
    login_command = f"{command} auth login --claudeai"
    if status.get("ready"):
        return {"started": False, "mode": "already_ready", "command": login_command, "status": status}
    if shutil.which("osascript"):
        script = (
            'tell application "Terminal"\n'
            f'  do script {json.dumps(login_command)}\n'
            "  activate\n"
            "end tell"
        )
        subprocess.Popen(["osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"started": True, "mode": "terminal", "command": login_command, "status": status}
    return {"started": False, "mode": "manual", "command": login_command, "status": status}


def _image_extension(mime: str) -> str:
    return _IMAGE_EXTENSIONS.get(mime.lower(), ".img")


class _ClaudeCodeImageContext:
    def __init__(self) -> None:
        self._tmp: tempfile.TemporaryDirectory[str] | None = None
        self.count = 0

    def close(self) -> None:
        if self._tmp is not None:
            self._tmp.cleanup()
            self._tmp = None

    @property
    def extra_dirs(self) -> list[str]:
        return [self._tmp.name] if self._tmp is not None else []

    def materialize(self, block: dict[str, Any]) -> str | None:
        image_url = str(block.get("image_url") or "").strip()
        match = _IMAGE_DATA_URL_RE.match(image_url)
        if not match:
            return None
        try:
            data = base64.b64decode(match.group(2), validate=True)
        except Exception:
            return None
        if not data:
            return None
        if self._tmp is None:
            self._tmp = tempfile.TemporaryDirectory(prefix="atrium-claude-code-images-")
        self.count += 1
        path = Path(self._tmp.name) / f"image-{self.count}{_image_extension(match.group(1))}"
        path.write_bytes(data)
        return str(path)


def _stringify_content(content: Any, image_context: _ClaudeCodeImageContext | None = None) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            parts.append(str(block))
            continue
        block_type = block.get("type")
        if block_type in {"text", "input_text"}:
            parts.append(str(block.get("text") or ""))
        elif block_type in {"tool_result", "function_call_output"}:
            parts.append(f"[tool result]\n{block.get('content') or block.get('output') or ''}")
        elif block_type in {"tool_use", "function_call"}:
            parts.append(f"[assistant tool request: {block.get('name') or 'tool'}]\n{json.dumps(block.get('input') or block.get('arguments') or {}, ensure_ascii=False)}")
        elif block_type in {"input_image", "image"}:
            path = image_context.materialize(block) if image_context is not None else None
            if path:
                parts.append(
                    "Claude Code image input "
                    f"{image_context.count}: @{path}\n"
                    "Inspect this local image file directly when the user asks about the attached image."
                )
            elif block.get("image_url"):
                image_url = str(block.get("image_url") or "")
                match = _IMAGE_DATA_URL_RE.match(image_url)
                if match:
                    approx_bytes = int(len(match.group(2)) * 0.75)
                    parts.append(f"Claude Code image input: {match.group(1)} data URL, approximately {approx_bytes} bytes")
                else:
                    parts.append(f"Claude Code image input is available at: {image_url}")
            elif block.get("file_id"):
                parts.append(f"Claude Code image input file_id: {block.get('file_id')}")
            else:
                parts.append("[image input present but could not be prepared for Claude Code]")
    return "\n".join(part for part in parts if part).strip()


def _text_from_json_payload(data: dict[str, Any]) -> str:
    for key in ("result", "text", "message", "content"):
        if key not in data:
            continue
        value = data.get(key)
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            return _stringify_content(value).strip()
        if isinstance(value, dict):
            content = value.get("content")
            if isinstance(content, (list, str)):
                text = _stringify_content(content).strip()
                if text:
                    return text
            for nested_key in ("text", "message", "result"):
                nested = value.get(nested_key)
                if isinstance(nested, str) and nested.strip():
                    return nested.strip()
    return ""


def _messages_prompt(messages: list[LLMMessage], image_context: _ClaudeCodeImageContext | None = None) -> str:
    lines: list[str] = []
    for message in messages:
        text = _stringify_content(message.content, image_context=image_context)
        if not text:
            continue
        role = "User" if message.role == "user" else "Assistant"
        lines.append(f"{role}:\n{text}")
    return "\n\n".join(lines).strip() or "Continue."


def _estimate_tokens(text: str) -> int:
    thai = sum(1 for ch in text if "\u0e00" <= ch <= "\u0e7f")
    non_thai = max(len(text) - thai, 0)
    return max(1, int(thai / 2.2 + non_thai / 4.0 + 0.999))


def _usage_int(usage: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = usage.get(key)
        try:
            parsed = int(value or 0)
        except (TypeError, ValueError):
            parsed = 0
        if parsed > 0:
            return parsed
    return 0


def _thinking_tokens(usage: dict[str, Any]) -> int:
    details = usage.get("output_tokens_details") if isinstance(usage.get("output_tokens_details"), dict) else {}
    return _usage_int(usage, "thinking_tokens", "reasoning_tokens") or _usage_int(
        details,
        "thinking_tokens",
        "reasoning_tokens",
    )


def _cli_model(model: str) -> str:
    normalized = (model or "").strip().lower()
    if "opus" in normalized:
        return "opus"
    if "sonnet" in normalized:
        return "sonnet"
    return (model or "").strip() or "sonnet"


def _available_tool_names(tools: list[dict[str, Any]] | None) -> set[str]:
    return {str(tool.get("name") or "").strip() for tool in tools or [] if str(tool.get("name") or "").strip()}


def _compact_tool_schema(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for tool in tools or []:
        name = str(tool.get("name") or "").strip()
        if not name:
            continue
        schema = tool.get("input_schema") or tool.get("parameters") or {}
        out.append({
            "name": name,
            "description": str(tool.get("description") or ""),
            "parameters": schema if isinstance(schema, dict) else {"type": "object", "properties": {}},
        })
    return out


def _tool_call_system_prompt(tools: list[dict[str, Any]] | None) -> str:
    compact = _compact_tool_schema(tools)
    return (
        "ATRIUM native tools are available in this turn. You cannot execute them inside the Claude Code CLI process; "
        "instead, request ATRIUM to execute them by returning a tool-call JSON object. "
        "When a tool is needed, return ONLY valid JSON in this shape: "
        '{"tool_calls":[{"id":"call_1","name":"tool_name","input":{}}]}. '
        "Use the exact tool name and JSON object input matching its parameters. "
        "After ATRIUM sends tool results back in the conversation, answer normally in the requested language. "
        "Available ATRIUM tools:\n"
        f"{json.dumps(compact, ensure_ascii=False)}"
    )


def _json_candidates(text: str) -> list[Any]:
    candidates: list[Any] = []
    raw = text.strip()
    if not raw:
        return candidates
    for candidate in (raw, re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE | re.DOTALL).strip()):
        if not candidate:
            continue
        with contextlib.suppress(Exception):
            candidates.append(json.loads(candidate))
    decoder = json.JSONDecoder()
    for idx, char in enumerate(raw):
        if char not in "[{":
            continue
        with contextlib.suppress(Exception):
            parsed, _end = decoder.raw_decode(raw[idx:])
            candidates.append(parsed)
    return candidates


def _tool_call_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    for key in ("tool_calls", "toolCalls", "function_calls", "functionCalls"):
        calls = value.get(key)
        if isinstance(calls, list):
            return [call for call in calls if isinstance(call, dict)]
        if isinstance(calls, dict):
            return [calls]
    for key in ("tool_call", "toolCall", "function_call", "functionCall"):
        call = value.get(key)
        if isinstance(call, dict):
            return [call]
    if value.get("name") or value.get("tool_name"):
        return [value]
    return []


def _tool_call_input(raw: dict[str, Any]) -> dict[str, Any]:
    args = raw.get("input")
    if args is None:
        args = raw.get("arguments")
    if isinstance(args, dict):
        return args
    if isinstance(args, str) and args.strip():
        with contextlib.suppress(Exception):
            parsed = json.loads(args)
            if isinstance(parsed, dict):
                return parsed
    return {}


def _extract_tool_calls(text: str, tools: list[dict[str, Any]] | None) -> list[LLMToolCall]:
    allowed = _available_tool_names(tools)
    if not allowed:
        return []
    calls: list[LLMToolCall] = []
    seen: set[tuple[str, str]] = set()
    for candidate in _json_candidates(text):
        for item in _tool_call_items(candidate):
            name = str(item.get("name") or item.get("tool_name") or "").strip()
            if not name or name not in allowed:
                continue
            call_id = str(item.get("id") or item.get("tool_call_id") or item.get("call_id") or f"claude_code_call_{len(calls) + 1}")
            key = (call_id, name)
            if key in seen:
                continue
            seen.add(key)
            calls.append(LLMToolCall(id=call_id, name=name, input=_tool_call_input(item)))
    return calls


class ClaudeCodeCliProvider:
    def __init__(self, provider_id: str, command: str = "claude", timeout: float | None = 300.0):
        self.id = provider_id
        self.live = True
        self.command = command
        self.timeout = timeout if timeout and timeout > 0 else None

    def _resolve_command(self) -> str:
        if "/" in self.command:
            if shutil.which(self.command) or Path(self.command).exists():
                return self.command
            raise RuntimeError(
                f"Claude Code CLI is required for provider claude_code; command path does not exist: {self.command}"
            )
        resolved = shutil.which(self.command)
        if not resolved:
            raise RuntimeError(
                f"Claude Code CLI is required for provider claude_code; install/login Claude Code or set ATRIUM_CLAUDE_CODE_COMMAND (current: {self.command})"
            )
        return resolved

    def _args(
        self,
        *,
        command: str,
        model: str,
        effort: str,
        system_prompt: str,
        stream: bool,
        extra_dirs: list[str] | None = None,
    ) -> list[str]:
        args = [
            command,
            "-p",
            "--no-session-persistence",
            "--permission-mode",
            "dontAsk",
            "--tools",
            "",
            "--model",
            _cli_model(model),
            "--effort",
            effort if effort in {"low", "medium", "high", "xhigh", "max"} else "high",
            "--system-prompt",
            system_prompt,
        ]
        for directory in extra_dirs or []:
            args[2:2] = ["--add-dir", directory]
        if stream:
            args[2:2] = ["--verbose", "--output-format", "stream-json", "--include-partial-messages"]
        else:
            args[2:2] = ["--output-format", "json"]
        return args

    async def count_context_tokens(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        model: str,
        effort: str = "high",
        speed: str = "standard",
        tools: list[dict[str, Any]] | None = None,
    ) -> int:
        tool_text = json.dumps(tools, ensure_ascii=False) if tools else ""
        return _estimate_tokens(system) + _estimate_tokens(_messages_prompt(messages)) + _estimate_tokens(tool_text)

    async def complete(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        model: str,
        effort: str = "high",
        speed: str = "standard",
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResult:
        command = self._resolve_command()
        image_context = _ClaudeCodeImageContext()
        try:
            prompt = _messages_prompt(messages, image_context=image_context)
            system_prompt = system
            if tools:
                system_prompt = f"{system_prompt}\n\n{_tool_call_system_prompt(tools)}"
            args = self._args(
                command=command,
                model=model,
                effort=effort,
                system_prompt=system_prompt,
                stream=False,
                extra_dirs=image_context.extra_dirs,
            )
            started = time.perf_counter()
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(prompt.encode("utf-8")), timeout=self.timeout)
            except asyncio.TimeoutError as exc:
                proc.kill()
                await proc.communicate()
                raise RuntimeError(f"Claude Code provider timed out after {self.timeout}s") from exc
            generation_ms = int((time.perf_counter() - started) * 1000)
            out = stdout.decode("utf-8", errors="replace").strip()
            err = stderr.decode("utf-8", errors="replace").strip()
            if proc.returncode != 0:
                detail = (err or out or f"exit {proc.returncode}")[:1200]
                raise RuntimeError(f"Claude Code provider failed: {detail}")
            data: dict[str, Any] = {}
            text = out
            try:
                parsed = json.loads(out)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                data = parsed
                if parsed.get("is_error") is True:
                    candidate = parsed.get("result") or parsed.get("message") or parsed.get("error") or out
                    raise RuntimeError(f"Claude Code provider failed: {str(candidate)[:1200]}")
                text = _text_from_json_payload(parsed)
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            tokens_in = _usage_int(usage, "input_tokens", "prompt_tokens")
            tokens_out = _usage_int(usage, "output_tokens", "completion_tokens")
            if tokens_in <= 0:
                tokens_in = _estimate_tokens(system_prompt) + _estimate_tokens(prompt)
            if tokens_out <= 0:
                tokens_out = _estimate_tokens(text)
            tool_calls = _extract_tool_calls(text, tools)
            content = [
                {"type": "tool_use", "id": call.id, "name": call.name, "input": call.input}
                for call in tool_calls
            ]
            return LLMResult(
                text="" if tool_calls else (text.strip() or "(ไม่มีเนื้อหาตอบกลับ)"),
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                thinking_tokens=_thinking_tokens(usage),
                generation_ms=generation_ms,
                model=str(data.get("model") or model),
                provider_id=self.id,
                speed="standard",
                stop_reason="tool_calls" if tool_calls else str(data.get("subtype") or data.get("stop_reason") or "end_turn"),
                content=content,
                tool_calls=tool_calls,
                meta={
                    "wireApi": "claude-code-cli",
                    "sessionId": data.get("session_id") or data.get("sessionId"),
                    "toolSupport": "atrium-json-shim" if tools else "text",
                    "imageInputs": image_context.count,
                },
            )
        finally:
            image_context.close()

    async def stream_complete(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        model: str,
        effort: str = "high",
        speed: str = "standard",
        tools: list[dict[str, Any]] | None = None,
    ):
        command = self._resolve_command()
        image_context = _ClaudeCodeImageContext()
        prompt = _messages_prompt(messages, image_context=image_context)
        system_prompt = system
        if tools:
            system_prompt = f"{system_prompt}\n\n{_tool_call_system_prompt(tools)}"
        args = self._args(
            command=command,
            model=model,
            effort=effort,
            system_prompt=system_prompt,
            stream=True,
            extra_dirs=image_context.extra_dirs,
        )
        started = time.perf_counter()
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert proc.stdin is not None
        assert proc.stdout is not None
        assert proc.stderr is not None
        stderr_task = asyncio.create_task(proc.stderr.read())
        assistant_message: dict[str, Any] = {}
        final_data: dict[str, Any] = {}
        final_usage: dict[str, Any] = {}
        final_stop_reason = "end_turn"
        text_parts: list[str] = []
        session_id: str | None = None
        model_name = model

        async def with_timeout(awaitable):
            if self.timeout is None:
                return await awaitable
            remaining = started + self.timeout - time.perf_counter()
            if remaining <= 0:
                raise asyncio.TimeoutError()
            return await asyncio.wait_for(awaitable, timeout=remaining)

        try:
            proc.stdin.write(prompt.encode("utf-8"))
            await with_timeout(proc.stdin.drain())
            proc.stdin.close()
            with contextlib.suppress(Exception):
                await with_timeout(proc.stdin.wait_closed())

            while True:
                line_bytes = await with_timeout(proc.stdout.readline())
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(item, dict):
                    continue
                if isinstance(item.get("session_id"), str):
                    session_id = item["session_id"]
                item_type = str(item.get("type") or "")
                if not item_type and any(key in item for key in ("result", "text", "message", "content")):
                    final_data = item
                    if item.get("is_error") is True:
                        candidate = item.get("result") or item.get("message") or item.get("error") or line
                        raise RuntimeError(f"Claude Code provider failed: {str(candidate)[:1200]}")
                    usage = item.get("usage") if isinstance(item.get("usage"), dict) else {}
                    if usage:
                        final_usage = usage
                    if item.get("stop_reason"):
                        final_stop_reason = str(item.get("stop_reason"))
                    if isinstance(item.get("model"), str):
                        model_name = str(item.get("model"))
                    continue
                if item_type == "stream_event":
                    event = item.get("event") if isinstance(item.get("event"), dict) else {}
                    event_type = str(event.get("type") or "")
                    if event_type == "message_start" and isinstance(event.get("message"), dict):
                        message = event["message"]
                        if isinstance(message.get("model"), str):
                            model_name = message["model"]
                        usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
                        if usage:
                            final_usage = usage
                    elif event_type == "content_block_delta":
                        delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
                        delta_type = str(delta.get("type") or "")
                        if delta_type == "text_delta":
                            text = str(delta.get("text") or "")
                            if text:
                                text_parts.append(text)
                                if not tools:
                                    yield LLMStreamEvent(kind="text_delta", text=text)
                        elif delta_type == "thinking_delta":
                            thinking = str(delta.get("thinking") or "")
                            if thinking:
                                yield LLMStreamEvent(kind="thinking_delta", text=thinking)
                    elif event_type == "message_delta":
                        usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
                        if usage:
                            final_usage = usage
                        delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
                        if delta.get("stop_reason"):
                            final_stop_reason = str(delta.get("stop_reason"))
                    continue
                if item_type == "assistant" and isinstance(item.get("message"), dict):
                    assistant_message = item["message"]
                    if isinstance(assistant_message.get("model"), str):
                        model_name = assistant_message["model"]
                    usage = assistant_message.get("usage") if isinstance(assistant_message.get("usage"), dict) else {}
                    if usage and not final_usage:
                        final_usage = usage
                    continue
                if item_type == "result":
                    final_data = item
                    if item.get("is_error") is True:
                        candidate = item.get("result") or item.get("message") or item.get("error") or line
                        raise RuntimeError(f"Claude Code provider failed: {str(candidate)[:1200]}")
                    usage = item.get("usage") if isinstance(item.get("usage"), dict) else {}
                    if usage:
                        final_usage = usage
                    if item.get("stop_reason"):
                        final_stop_reason = str(item.get("stop_reason"))
                    model_usage = item.get("modelUsage") if isinstance(item.get("modelUsage"), dict) else {}
                    if model_usage:
                        model_name = next(iter(model_usage.keys()), model_name)

            returncode = await with_timeout(proc.wait())
            err = (await stderr_task).decode("utf-8", errors="replace").strip()
            if returncode != 0:
                detail = (err or final_data.get("result") or f"exit {returncode}")[:1200]
                raise RuntimeError(f"Claude Code provider failed: {detail}")
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.communicate()
            raise RuntimeError(f"Claude Code provider timed out after {self.timeout}s") from exc
        finally:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
            image_context.close()

        generation_ms = int((time.perf_counter() - started) * 1000)
        text = _text_from_json_payload(final_data)
        if not text:
            candidate = assistant_message.get("content")
            if isinstance(candidate, list):
                text = _stringify_content(candidate)
        if not text:
            text = "".join(text_parts).strip()
        tool_calls = _extract_tool_calls(text, tools)
        content = [
            {"type": "tool_use", "id": call.id, "name": call.name, "input": call.input}
            for call in tool_calls
        ]
        tokens_in = _usage_int(final_usage, "input_tokens", "prompt_tokens")
        tokens_out = _usage_int(final_usage, "output_tokens", "completion_tokens")
        if tokens_in <= 0:
            tokens_in = _estimate_tokens(system_prompt) + _estimate_tokens(prompt)
        if tokens_out <= 0:
            tokens_out = _estimate_tokens(text)
        result = LLMResult(
            text="" if tool_calls else (text or "(ไม่มีเนื้อหาตอบกลับ)"),
            tokens_in=tokens_in,
            tokens_out=max(tokens_out - _thinking_tokens(final_usage), 0),
            thinking_tokens=_thinking_tokens(final_usage),
            generation_ms=generation_ms,
            model=str(model_name or model),
            provider_id=self.id,
            speed=str((final_data.get("usage") or {}).get("speed") or "standard") if isinstance(final_data.get("usage"), dict) else "standard",
            stop_reason="tool_calls" if tool_calls else final_stop_reason,
            content=content,
            tool_calls=tool_calls,
            meta={
                "wireApi": "claude-code-cli",
                "sessionId": final_data.get("session_id") or session_id,
                "toolSupport": "atrium-json-shim" if tools else "text",
                "imageInputs": image_context.count,
                "stream": True,
            },
        )
        if result.text and (tools or not text_parts):
            yield LLMStreamEvent(kind="text_delta", text=result.text)
        yield LLMStreamEvent(kind="message_stop", result=result)
