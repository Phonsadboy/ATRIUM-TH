"""Native runtime turn compatibility helpers.

ATRIUM now runs chat/work through provider-native paths in `main.py` and
`engine.py`. This module keeps the older runtime helper API as a no-external-
runtime compatibility layer while shared metadata/text helpers remain usable.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
import re
from typing import Any

from ..catalog import DEFAULT_MODEL, coerce_model_speed
from ..config import Settings, get_settings
from ..provider.base import LLMMessage, LLMResult, LLMStreamEvent, LLMToolCall


class RuntimeTurnUnavailable(RuntimeError):
    """Raised when a configured runtime cannot complete a turn."""


def _runtime_agent_key(dept: dict[str, Any]) -> str:
    return "executive" if dept.get("id") == "exec" else str(dept.get("id") or "").strip()


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
        text="ATRIUM native runtime พร้อมใช้งานผ่าน provider-native path แล้ว แต่ helper runtime เดิมไม่ควรถูกเรียกใน path นี้",
        tokens_in=0,
        tokens_out=0,
        model=model,
        provider_id="native",
        speed=coerce_model_speed(model, dept.get("speed", "standard")),
        stop_reason="runtime_dependency",
        meta={
            "runtimeBackend": "native",
            "configuredBackend": settings.agent_backend_mode,
            "runtimeAgentKey": _runtime_agent_key(dept),
            "runtimeAgentId": None,
            "runtimeDependency": True,
            "runtimeError": detail,
            "runtimeCategory": category,
            "runtimeSource": source,
            "externalRuntime": False,
        },
    )


def runtime_result_metadata(result: LLMResult, dept: dict[str, Any]) -> dict[str, Any] | None:
    backend = result.meta.get("runtimeBackend")
    if not backend:
        return None
    tool_runs = result.meta.get("toolRuns")
    tool_run_count = len(tool_runs) if isinstance(tool_runs, list) else 0
    payload = {
        "backend": backend,
        "agentKey": result.meta.get("runtimeAgentKey") or _runtime_agent_key(dept),
        "runtimeAgentId": result.meta.get("runtimeAgentId"),
        "departmentId": dept.get("id"),
        "toolRounds": result.meta.get("runtimeToolRounds"),
        "toolRunCount": tool_run_count,
        "externalRuntime": bool(result.meta.get("externalRuntime", False)),
        **({"error": result.meta.get("runtimeError")} if result.meta.get("runtimeError") else {}),
    }
    if result.meta.get("runtimeDependency"):
        payload["dependency"] = True
        payload["category"] = result.meta.get("runtimeCategory")
        payload["source"] = result.meta.get("runtimeSource")
    return payload


def _image_block_to_text(block: dict[str, Any]) -> str:
    url = str(block.get("image_url") or block.get("url") or "")
    file_id = str(block.get("file_id") or "")
    detail = str(block.get("detail") or "").strip()
    mime = "image"
    approx = ""
    if url.startswith("data:"):
        match = re.match(r"data:([^;,]+)", url)
        if match:
            mime = match.group(1)
        if "," in url:
            approx = f"; approxBase64Chars={len(url.split(',', 1)[1])}"
    elif url:
        mime = "image URL"
    elif file_id:
        mime = "image file"
    detail_text = f"; detail={detail}" if detail else ""
    source = "file_id" if file_id else "data URL" if url.startswith("data:") else "url" if url else "unknown"
    return f"[attached {mime} omitted from ATRIUM native runtime text prompt{detail_text}{approx}; source={source}]"


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
                elif item:
                    parts.append(str(item))
            elif item is not None:
                parts.append(str(item))
        return "\n".join(parts)
    return "" if content is None else str(content)


async def runtime_recall_snippet(
    dept: dict[str, Any],
    query: str,
    *,
    settings: Settings | None = None,
    limit: int = 6,
) -> str:
    del dept, query, settings, limit
    return ""


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
    on_runtime_event: Callable[[Any], Awaitable[None]] | None = None,
    max_tool_rounds: int | None = None,
    required_tools: list[str] | None = None,
    settings: Settings | None = None,
    allow_provider_fallback: bool = True,
) -> LLMResult | None:
    del (
        dept,
        user_text,
        thread_id,
        memory_context,
        system_prompt,
        messages,
        metadata,
        on_stream_event,
        client_tools,
        tool_executor,
        on_runtime_event,
        max_tool_rounds,
        required_tools,
        settings,
        allow_provider_fallback,
    )
    return None
