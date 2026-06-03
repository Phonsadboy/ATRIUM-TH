"""Executive agent runtime compatibility helpers."""
from __future__ import annotations

from typing import Any, Callable, Awaitable

from ..config import Settings, get_settings
from ..memory.company_memory import ensure_company_memory_files
from ..provider.base import LLMResult
from .provisioning import ensure_department_runtime_agent
from .turns import complete_agent_via_runtime, runtime_recall_snippet

EXECUTIVE_AGENT_KEY = "executive"


async def ensure_executive_runtime_agent(
    repo: Any,
    dept: dict[str, Any],
    *,
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    settings = settings or get_settings()
    if not settings.use_letta_runtime:
        return None
    ensure_company_memory_files(settings)
    return await ensure_department_runtime_agent(repo, dept, settings=settings)


async def letta_recall_snippet(
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
    """Stream an executive turn through Letta when configured. Returns None to fall back to v1 engine."""
    return await complete_agent_via_runtime(
        dept,
        user_text=user_text,
        thread_id=thread_id,
        memory_context=memory_context,
        metadata={"compat": "executive"},
        on_stream_event=on_stream_event,
        settings=get_settings(),
    )
