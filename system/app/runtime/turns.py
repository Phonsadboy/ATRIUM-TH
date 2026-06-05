"""Generic agent-runtime turn helpers for ATRIUM v2."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
import json
import re
from typing import Any

from ..catalog import DEFAULT_MODEL, coerce_model_speed, coerce_thinking_effort
from ..clock import now_ms
from ..config import Settings, get_settings
from ..provider.base import LLMMessage, LLMResult, LLMStreamEvent, LLMToolCall
from .base import RuntimeEvent
from .factory import get_agent_runtime
from .letta_adapter import LettaRuntimeAdapter
from .provisioning import runtime_agent_key


class RuntimeTurnUnavailable(RuntimeError):
    """Raised when a configured stateful agent runtime cannot complete a turn."""


def runtime_dependency_result(
    dept: dict[str, Any],
    detail: str,
    *,
    category: str = "chat",
    source: str = "runtime",
    settings: Settings | None = None,
) -> LLMResult:
    settings = settings or get_settings()
    model = str(dept.get("model") or DEFAULT_MODEL)
    return LLMResult(
        text="Agent runtime ยังไม่พร้อม ระบบยังไม่ fallback ไป provider เดิมเพื่อรักษา stateful agent context",
        tokens_in=0,
        tokens_out=0,
        model=model,
        provider_id="letta",
        speed=coerce_model_speed(model, dept.get("speed", "standard")),
        stop_reason="runtime_dependency",
        meta={
            "runtimeBackend": settings.agent_backend or "letta",
            "runtimeAgentKey": (dept.get("runtime") or {}).get("agentKey") or runtime_agent_key(dept),
            "runtimeAgentId": (dept.get("runtime") or {}).get("lettaAgentId"),
            "runtimeDependency": True,
            "runtimeError": detail,
            "runtimeCategory": category,
            "runtimeSource": source,
        },
    )


def bind_runtime_agent(runtime: LettaRuntimeAdapter, dept: dict[str, Any]) -> str | None:
    """Bind a persisted ATRIUM department runtime record into the adapter cache."""
    runtime_info = dept.get("runtime") if isinstance(dept.get("runtime"), dict) else {}
    agent_id = runtime_info.get("lettaAgentId")
    if not agent_id:
        return None
    agent_key = str(runtime_info.get("agentKey") or runtime_agent_key(dept))
    if not agent_key:
        return None
    runtime._agent_ids[agent_key] = str(agent_id)
    runtime.bind_department(agent_key, dept)
    return agent_key


def runtime_result_metadata(result: LLMResult, dept: dict[str, Any]) -> dict[str, Any] | None:
    backend = result.meta.get("runtimeBackend")
    if not backend:
        return None
    runtime_info = dept.get("runtime") if isinstance(dept.get("runtime"), dict) else {}
    tool_runs = result.meta.get("toolRuns")
    tool_run_count = len(tool_runs) if isinstance(tool_runs, list) else 0
    payload = {
        "backend": backend,
        "agentKey": result.meta.get("runtimeAgentKey") or runtime_info.get("agentKey"),
        "runtimeAgentId": result.meta.get("runtimeAgentId") or runtime_info.get("lettaAgentId"),
        "departmentId": dept.get("id"),
        "toolRounds": result.meta.get("runtimeToolRounds"),
        "toolRunCount": tool_run_count,
        **({"providerId": result.meta.get("runtimeProviderId")} if result.meta.get("runtimeProviderId") else {}),
        **({"wireApi": result.meta.get("runtimeWireApi")} if result.meta.get("runtimeWireApi") else {}),
        **({"model": result.meta.get("runtimeModel")} if result.meta.get("runtimeModel") else {}),
        **({"memoryArchived": result.meta.get("runtimeMemoryArchived")} if "runtimeMemoryArchived" in result.meta else {}),
        **({"providerFallbackFrom": result.meta.get("runtimeProviderFallbackFrom")} if result.meta.get("runtimeProviderFallbackFrom") else {}),
        **({"providerFallbackTo": result.meta.get("runtimeProviderFallbackTo")} if result.meta.get("runtimeProviderFallbackTo") else {}),
        **({"providerFallbackError": result.meta.get("runtimeProviderFallbackError")} if result.meta.get("runtimeProviderFallbackError") else {}),
        **({"providerRetryCount": result.meta.get("runtimeProviderRetryCount")} if "runtimeProviderRetryCount" in result.meta else {}),
        **({"error": result.meta.get("runtimeError")} if result.meta.get("runtimeError") else {}),
    }
    if result.meta.get("runtimeDependency"):
        payload["dependency"] = True
        payload["category"] = result.meta.get("runtimeCategory")
        payload["source"] = result.meta.get("runtimeSource")
    return payload


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text" and item.get("text"):
                    parts.append(str(item["text"]))
                elif item.get("type") in {"input_image", "image_url"}:
                    parts.append(_image_block_to_text(item))
                elif item.get("text"):
                    parts.append(str(item["text"]))
                else:
                    parts.append(json.dumps(item, ensure_ascii=False, sort_keys=True))
            elif item is not None:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content or "")


def _image_block_to_text(item: dict[str, Any]) -> str:
    raw_url = str(item.get("image_url") or item.get("url") or "")
    mime = "image"
    byte_hint = ""
    if raw_url.startswith("data:"):
        header, _, data = raw_url.partition(",")
        declared = header[5:].split(";", 1)[0].strip()
        if declared:
            mime = declared
        if data:
            byte_hint = f", approxBase64Chars={len(data)}"
    elif raw_url:
        byte_hint = ", url omitted from runtime prompt"
    detail = str(item.get("detail") or "").strip()
    detail_text = f", detail={detail}" if detail else ""
    return (
        f"[attached {mime} omitted from Letta runtime text prompt{detail_text}{byte_hint}; "
        "use the adjacent attachment metadata or artifact id if visual inspection is required]"
    )


def _serialize_messages(messages: list[LLMMessage] | None) -> str:
    if not messages:
        return ""
    lines: list[str] = []
    for msg in messages:
        lines.append(f"{msg.role.upper()}:\n{_content_to_text(msg.content)}")
    return "\n\n".join(lines)


def _build_runtime_prompt(
    *,
    user_text: str,
    system_prompt: str = "",
    messages: list[LLMMessage] | None = None,
    memory_context: str = "",
    recall: str = "",
) -> str:
    parts: list[str] = []
    if system_prompt.strip():
        parts.append("System instructions:\n" + system_prompt.strip())
    serialized = _serialize_messages(messages)
    if serialized:
        parts.append("Conversation/input messages:\n" + serialized)
    elif user_text.strip():
        parts.append("User request:\n" + user_text.strip())
    if memory_context.strip() or recall.strip():
        parts.append("Context for this turn:\n" + "\n\n".join(p for p in (memory_context.strip(), recall.strip()) if p))
    return "\n\n---\n\n".join(parts).strip()


def runtime_client_tool_schema(tool: dict[str, Any]) -> dict[str, Any]:
    """Convert an ATRIUM chat-tool definition into Letta's client_tool schema."""
    params = tool.get("input_schema")
    if not isinstance(params, dict):
        params = {"type": "object", "properties": {}}
    return {
        "name": str(tool.get("name") or ""),
        "description": str(tool.get("description") or ""),
        "parameters": params,
    }


def runtime_client_tool_schemas(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tool in tools or []:
        name = str(tool.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(runtime_client_tool_schema(tool))
    return out


def _parse_tool_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _iter_tool_call_payloads(message: dict[str, Any]) -> list[dict[str, Any]]:
    calls = message.get("tool_calls")
    if isinstance(calls, list):
        return [call for call in calls if isinstance(call, dict)]
    if isinstance(calls, dict):
        return [calls]
    call = message.get("tool_call")
    return [call] if isinstance(call, dict) else []


def _runtime_tool_calls(payload: dict[str, Any], allowed_tools: set[str]) -> list[LLMToolCall]:
    out: list[LLMToolCall] = []
    for message in payload.get("messages") or []:
        if not isinstance(message, dict):
            continue
        if message.get("message_type") not in {"approval_request_message", "tool_call_message"}:
            continue
        for call in _iter_tool_call_payloads(message):
            name = str(call.get("name") or call.get("tool_name") or "").strip()
            if not name or (allowed_tools and name not in allowed_tools):
                continue
            call_id = str(call.get("tool_call_id") or call.get("id") or f"runtime_tool_{len(out)+1}")
            out.append(LLMToolCall(id=call_id, name=name, input=_parse_tool_args(call.get("arguments") or call.get("input"))))
    return out


def _current_user_text(user_text: str, messages: list[LLMMessage] | None) -> str:
    if user_text.strip():
        return user_text
    if not messages:
        return ""
    for msg in reversed(messages):
        if msg.role == "user":
            return _content_to_text(msg.content)
    return ""


def _explicit_required_tools(text: str, allowed_tools: set[str]) -> set[str]:
    lowered = str(text or "").lower()
    if not lowered or not allowed_tools:
        return set()
    directive_markers = (
        "must call",
        "must use",
        "use the",
        "use tool",
        "call the",
        "exactly once",
        "before answering",
        "only with",
        "ใช้เครื่องมือ",
        "เรียกเครื่องมือ",
        "ต้องใช้",
        "ต้องเรียก",
        "ก่อนตอบ",
        "เท่านั้น",
    )
    required: set[str] = set()
    for tool in allowed_tools:
        needle = tool.lower()
        if needle not in lowered:
            continue
        idx = lowered.find(needle)
        window = lowered[max(0, idx - 80): idx + len(needle) + 80]
        if any(marker in window for marker in directive_markers):
            required.add(tool)
    return required


def _required_tool_retry_prompt(required_tools: set[str]) -> str:
    names = ", ".join(sorted(required_tools))
    return (
        "Your previous response did not satisfy an explicit tool requirement. "
        f"Call the required tool now before writing any final answer. Required tool(s): {names}. "
        "Do not answer from memory or inference."
    )


def _strip_path(raw: str) -> str:
    return raw.strip().rstrip(".,);]'\"")


def _deterministic_required_tool_calls(text: str, required_tools: set[str]) -> list[LLMToolCall]:
    if "call_atrium_api" not in required_tools:
        return []
    raw = str(text or "")
    path_match = re.search(r"(/api/[A-Za-z0-9_./{}:\-?=&%]+|/health)\b", raw)
    if not path_match:
        return []
    method_match = re.search(r"\b(GET|POST|PATCH|PUT|DELETE)\b", raw, re.IGNORECASE)
    method = method_match.group(1).upper() if method_match else "GET"
    path = _strip_path(path_match.group(1))
    query: dict[str, Any] = {}
    if "?" in path:
        path, raw_query = path.split("?", 1)
        for part in raw_query.split("&"):
            if not part:
                continue
            key, _, value = part.partition("=")
            if key:
                query[key] = value
    payload = {"method": method, "path": path}
    if query:
        payload["query"] = query
    return [LLMToolCall(id="forced_call_atrium_api_1", name="call_atrium_api", input=payload)]


def _tool_return_message(records: list[dict[str, Any]]) -> dict[str, Any]:
    returns: list[dict[str, Any]] = []
    for record in records:
        ok = record.get("status") == "succeeded"
        payload = record.get("result") if ok else {"ok": False, "error": record.get("error")}
        returns.append({
            "type": "tool",
            "tool_call_id": str(record.get("toolUseId") or record.get("id") or ""),
            "name": str(record.get("tool") or ""),
            "arguments": json.dumps(record.get("args") or {}, ensure_ascii=False, default=str),
            "status": "success" if ok else "error",
            "tool_return": json.dumps(payload, ensure_ascii=False, default=str),
            **({"stderr": [str(record.get("error"))]} if record.get("error") else {}),
        })
    return {"type": "tool_return", "tool_returns": returns}


def _fallback_tool_response(tool_runs: list[dict[str, Any]], error_message: str | None) -> str:
    lines: list[str] = []
    for run in tool_runs[:5]:
        tool = str(run.get("tool") or "tool")
        status = str(run.get("status") or "unknown")
        result = run.get("result") if isinstance(run.get("result"), dict) else {}
        summary = str(result.get("summary") or result.get("tool") or "").strip()
        if summary:
            lines.append(f"- {tool}: {status} ({summary})")
        else:
            lines.append(f"- {tool}: {status}")
    if error_message:
        lines.append(f"- runtime: {error_message}")
    if not lines:
        return ""
    return "ดำเนินการผ่าน runtime tool แล้ว แต่ runtime ไม่ส่งข้อความสรุปกลับมา:\n" + "\n".join(lines)


async def runtime_recall_snippet(
    dept: dict[str, Any],
    query: str,
    *,
    settings: Settings | None = None,
    limit: int = 6,
) -> str:
    settings = settings or get_settings()
    if not settings.use_letta_runtime:
        return ""
    runtime = get_agent_runtime(settings)
    if not isinstance(runtime, LettaRuntimeAdapter):
        return ""
    agent_key = bind_runtime_agent(runtime, dept)
    if not agent_key:
        return ""
    try:
        rows = await runtime.recall(agent_key, query=query, limit=limit)
    except Exception:
        return ""
    lines: list[str] = []
    for row in rows[:limit]:
        if isinstance(row, dict):
            text = row.get("text") or row.get("content") or row.get("passage")
            if text:
                lines.append(f"- {str(text)[:400]}")
        elif row:
            lines.append(f"- {str(row)[:400]}")
    if not lines:
        return ""
    return "Runtime archival recall:\n" + "\n".join(lines)


async def complete_agent_via_runtime(
    dept: dict[str, Any],
    *,
    user_text: str = "",
    thread_id: str | None = None,
    memory_context: str = "",
    system_prompt: str = "",
    messages: list[LLMMessage] | None = None,
    metadata: dict[str, Any] | None = None,
    on_stream_event: Callable[[LLMStreamEvent], Awaitable[None]] | None = None,
    client_tools: list[dict[str, Any]] | None = None,
    tool_executor: Callable[[LLMToolCall], Awaitable[dict[str, Any]]] | None = None,
    on_runtime_event: Callable[[RuntimeEvent], Awaitable[None]] | None = None,
    max_tool_rounds: int | None = None,
    required_tools: list[str] | None = None,
    settings: Settings | None = None,
    allow_provider_fallback: bool = True,
) -> LLMResult | None:
    """Complete one agent turn through the configured stateful runtime.

    Returns None when runtime usage is disabled. During cutover callers may also
    opt into None for unavailable/no-text turns so the v1 provider loop can run.
    Production agent chat/work paths pass allow_provider_fallback=False so a
    configured runtime failure remains a runtime dependency instead of silently
    losing the stateful agent context.
    """
    settings = settings or get_settings()
    tool_round_limit = (
        settings.runtime_max_tool_round_limit
        if max_tool_rounds is None
        else max(0, int(max_tool_rounds))
    )
    if not settings.use_letta_runtime:
        return None
    runtime = get_agent_runtime(settings)
    if not isinstance(runtime, LettaRuntimeAdapter):
        if not allow_provider_fallback:
            raise RuntimeTurnUnavailable("runtime adapter is not Letta")
        return None
    agent_key = bind_runtime_agent(runtime, dept)
    if not agent_key:
        if not allow_provider_fallback:
            raise RuntimeTurnUnavailable(f"runtime agent is not bound for department {dept.get('id') or 'unknown'}")
        return None
    runtime_info = dept.get("runtime") if isinstance(dept.get("runtime"), dict) else {}

    recall_query = user_text or "\n".join(_content_to_text(m.content) for m in (messages or [])[-2:])
    recall = await runtime_recall_snippet(dept, recall_query, settings=settings) if recall_query else ""
    prompt = _build_runtime_prompt(
        user_text=user_text,
        system_prompt=system_prompt,
        messages=messages,
        memory_context=memory_context,
        recall=recall,
    )
    if not prompt:
        if not allow_provider_fallback:
            raise RuntimeTurnUnavailable("runtime prompt is empty")
        return None

    model = dept.get("model") or DEFAULT_MODEL
    effort = coerce_thinking_effort(model, dept.get("thinkingEffort", "high"))
    speed = coerce_model_speed(model, dept.get("speed", "standard"))
    started = now_ms()
    text_parts: list[str] = []
    error_message: str | None = None
    client_tool_schemas = runtime_client_tool_schemas(client_tools)
    allowed_tools = {tool["name"] for tool in client_tool_schemas}
    explicit_required_tools = {name for name in (required_tools or []) if name in allowed_tools}
    current_user_text = _current_user_text(user_text, messages)
    explicit_required_tools.update(_explicit_required_tools(current_user_text, allowed_tools))
    tool_runs: list[dict[str, Any]] = []
    usage_payloads: list[dict[str, Any]] = []
    satisfied_required_tools: set[str] = set()
    forced_tool_names: set[str] = set()
    rounds = 0
    runtime_payload_meta: dict[str, Any] = {}

    async def handle_payload(payload: dict[str, Any]) -> None:
        nonlocal error_message
        usage = payload.get("usage")
        if isinstance(usage, dict):
            usage_payloads.append(usage)
        for source_key, meta_key in (
            ("runtimeProviderId", "runtimeProviderId"),
            ("runtimeWireApi", "runtimeWireApi"),
            ("runtimeModel", "runtimeModel"),
            ("runtimeStopReason", "runtimeStopReason"),
            ("reasoningStatus", "runtimeReasoningStatus"),
            ("runtimeMemoryArchived", "runtimeMemoryArchived"),
            ("runtimeProviderFallbackFrom", "runtimeProviderFallbackFrom"),
            ("runtimeProviderFallbackTo", "runtimeProviderFallbackTo"),
            ("runtimeProviderFallbackError", "runtimeProviderFallbackError"),
            ("runtimeProviderRetryCount", "runtimeProviderRetryCount"),
        ):
            if source_key in payload:
                runtime_payload_meta[meta_key] = payload.get(source_key)
        text = _assistant_text(payload)
        if text:
            text_parts.append(text)
            if on_stream_event is not None:
                await on_stream_event(LLMStreamEvent(kind="text_delta", text=text))
            elif on_runtime_event is not None:
                await on_runtime_event(RuntimeEvent("assistant_message", {"text": text}))

    async def execute_tool_calls(tool_calls: list[LLMToolCall], *, forced: bool = False) -> list[dict[str, Any]]:
        nonlocal rounds
        rounds += 1
        records: list[dict[str, Any]] = []
        for call in tool_calls:
            if call.name in explicit_required_tools:
                satisfied_required_tools.add(call.name)
            if forced:
                forced_tool_names.add(call.name)
            if on_runtime_event is not None:
                await on_runtime_event(RuntimeEvent("tool_call", {
                    "id": call.id,
                    "toolUseId": call.id,
                    "tool": call.name,
                    "departmentId": dept.get("id"),
                    "args": call.input,
                    "status": "running",
                    "startedAt": now_ms(),
                    **({"forced": True} if forced else {}),
                }))
            record = await tool_executor(call) if tool_executor is not None else {}
            records.append(record)
            if on_runtime_event is not None:
                await on_runtime_event(RuntimeEvent("tool_result", {"run": record, **({"forced": True} if forced else {})}))
        tool_runs.extend(records)
        return records

    async def emit_provider_status(payload: dict[str, Any]) -> None:
        if on_runtime_event is not None:
            await on_runtime_event(RuntimeEvent("status", payload))
            return
        message = str(payload.get("message") or "").strip()
        if on_stream_event is not None and message:
            await on_stream_event(LLMStreamEvent(kind="thinking_delta", text=f"\n\n**สถานะ provider**\n{message}"))

    provider_status_callback = (
        emit_provider_status
        if on_runtime_event is not None or on_stream_event is not None
        else None
    )

    enforcement_retries = 0
    try:
        preflight_forced_calls = (
            _deterministic_required_tool_calls(current_user_text, explicit_required_tools)
            if tool_executor is not None and explicit_required_tools
            else []
        )
        if preflight_forced_calls:
            await execute_tool_calls(preflight_forced_calls, forced=True)
        else:
            payload = await runtime.send_message(
                agent_key,
                message=prompt,
                thread_id=thread_id,
                metadata={"departmentId": dept.get("id"), **(metadata or {})},
                client_tools=client_tool_schemas or None,
                max_steps=1 if client_tool_schemas else None,
                status_callback=provider_status_callback,
            )
            while True:
                tool_calls = _runtime_tool_calls(payload, allowed_tools)
                if (
                    tool_executor is not None
                    and explicit_required_tools
                    and not tool_calls
                    and not explicit_required_tools.issubset(satisfied_required_tools)
                ):
                    tool_calls = _deterministic_required_tool_calls(
                        current_user_text,
                        explicit_required_tools - satisfied_required_tools,
                    )
                if (
                    tool_executor is not None
                    and explicit_required_tools
                    and not tool_calls
                    and not explicit_required_tools.issubset(satisfied_required_tools)
                    and enforcement_retries < 2
                ):
                    enforcement_retries += 1
                    text_parts.clear()
                    payload = await runtime.send_message(
                        agent_key,
                        message=_required_tool_retry_prompt(explicit_required_tools - satisfied_required_tools),
                        thread_id=thread_id,
                        metadata={"departmentId": dept.get("id"), **(metadata or {}), "toolRequirementRetry": enforcement_retries},
                        client_tools=client_tool_schemas or None,
                        max_steps=1,
                        status_callback=provider_status_callback,
                    )
                    continue
                if not tool_calls:
                    await handle_payload(payload)
                    break
                await handle_payload(payload)
                forced_this_round = all(call.id.startswith("forced_") for call in tool_calls)
                records = await execute_tool_calls(tool_calls, forced=forced_this_round)
                if forced_this_round:
                    break
                payload = await runtime.send_message(
                    agent_key,
                    messages=[_tool_return_message(records)],
                    thread_id=thread_id,
                    metadata={"departmentId": dept.get("id"), **(metadata or {})},
                    max_steps=2,
                    status_callback=provider_status_callback,
                )
                if tool_round_limit > 0 and rounds >= tool_round_limit:
                    break
    except Exception as exc:
        error_message = f"{type(exc).__name__}: {exc}"
        if on_runtime_event is not None:
            await on_runtime_event(RuntimeEvent("error", {"message": error_message}))

    missing_required_tools = sorted(explicit_required_tools - satisfied_required_tools)
    if missing_required_tools and tool_executor is not None:
        forced_calls = _deterministic_required_tool_calls(current_user_text, set(missing_required_tools))
        if forced_calls:
            await execute_tool_calls(forced_calls, forced=True)

    missing_required_tools = sorted(explicit_required_tools - satisfied_required_tools)
    if missing_required_tools:
        error_message = f"runtime did not call required tool(s): {', '.join(missing_required_tools)}"
        if on_runtime_event is not None:
            await on_runtime_event(RuntimeEvent("error", {"message": error_message}))
        if not allow_provider_fallback:
            raise RuntimeTurnUnavailable(error_message)
        return None

    final_text = "".join(text_parts).strip()
    if not final_text and tool_runs:
        final_text = _fallback_tool_response(tool_runs, error_message)
    if not final_text:
        if on_runtime_event is not None:
            await on_runtime_event(RuntimeEvent("done", {"failed": True}))
        if not allow_provider_fallback:
            raise RuntimeTurnUnavailable(error_message or "runtime produced no assistant text")
        return None
    tokens_in = max(1, len(prompt) // 4)
    tokens_out = max(1, len(final_text) // 4)
    thinking_tokens = 0
    if usage_payloads:
        tokens_in = sum(int(item.get("prompt_tokens") or 0) for item in usage_payloads) or tokens_in
        completion_tokens = sum(int(item.get("completion_tokens") or 0) for item in usage_payloads)
        thinking_tokens = sum(
            int(
                (
                    item.get("output_tokens_details")
                    if isinstance(item.get("output_tokens_details"), dict)
                    else item.get("completion_tokens_details")
                    if isinstance(item.get("completion_tokens_details"), dict)
                    else {}
                ).get("reasoning_tokens")
                or 0
            )
            for item in usage_payloads
        )
        tokens_out = max(completion_tokens - thinking_tokens, 0) or tokens_out
    result = LLMResult(
        text=final_text,
        provider_id="letta",
        model=str(model),
        speed=speed,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        thinking_tokens=thinking_tokens,
        generation_ms=max(1, now_ms() - started),
        meta={
            "runtimeBackend": "letta",
            "runtimeAgentKey": agent_key,
            "runtimeAgentId": runtime_info.get("lettaAgentId"),
            "runtimeError": error_message,
            "thinkingEffort": effort,
            "toolRuns": tool_runs,
            "runtimeToolRounds": rounds,
            "runtimeMaxToolRounds": tool_round_limit or None,
            "runtimeRequiredTools": sorted(explicit_required_tools),
            "runtimeForcedTools": sorted(forced_tool_names),
            "runtimeToolRequirementRetries": enforcement_retries,
            **runtime_payload_meta,
        },
    )
    if on_runtime_event is not None:
        await on_runtime_event(RuntimeEvent("usage", {
            "tokensIn": tokens_in,
            "tokensOut": tokens_out,
            "toolRounds": rounds,
        }))
        await on_runtime_event(RuntimeEvent("done", {"failed": False}))
    return result


def _assistant_text(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    for message in payload.get("messages") or []:
        if not isinstance(message, dict) or message.get("message_type") != "assistant_message":
            continue
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            text_parts = [
                str(item.get("text"))
                for item in content
                if isinstance(item, dict) and item.get("text")
            ]
            if text_parts:
                parts.append("\n".join(text_parts))
    return "\n".join(part for part in parts if part).strip()
