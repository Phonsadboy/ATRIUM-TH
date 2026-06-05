"""Executive native-runtime compatibility helpers."""
from __future__ import annotations

from typing import Any, Callable, Awaitable

from ..config import Settings, get_settings
from ..memory.company_memory import ensure_company_memory_files
from ..provider.base import LLMResult
from .turns import complete_agent_via_runtime, runtime_recall_snippet

EXECUTIVE_AGENT_KEY = "executive"


async def ensure_executive_runtime_agent(
    repo: Any,
    dept: dict[str, Any],
    *,
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    settings = settings or get_settings()
    del repo
    ensure_company_memory_files(settings)
    return {
        "backend": "native",
        "agentKey": EXECUTIVE_AGENT_KEY,
        "departmentId": dept.get("id"),
        "status": "ready",
        "externalRuntime": False,
        "provisioningRequired": False,
    }


async def runtime_recall_compat_snippet(
    dept: dict[str, Any],
    query: str,
    *,
    settings: Settings | None = None,
    limit: int = 6,
) -> str:
    return await runtime_recall_snippet(dept, query, settings=settings, limit=limit)
async def complete_executive_via_runtime(
    dept: dict[str, Any],
    *,
    user_text: str,
    thread_id: str,
    memory_context: str,
    on_stream_event: Callable[[Any], Awaitable[None]] | None = None,
) -> LLMResult | None:
    """Compatibility wrapper; native chat uses provider paths and returns None."""
    return await complete_agent_via_runtime(
        dept,
        user_text=user_text,
        thread_id=thread_id,
        memory_context=memory_context,
        metadata={"compat": "executive"},
        on_stream_event=on_stream_event,
        settings=get_settings(),
    )
