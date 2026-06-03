"""Owner profile (USER.md) and company memory (MEMORY.md) for the Executive."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..clock import now_ms
from ..config import Settings, get_settings

USER_FILENAME = "USER.md"
MEMORY_FILENAME = "MEMORY.md"

_DEFAULT_USER = """# Owner profile

- Language: Thai for day-to-day updates; English for code and APIs when clearer.
- Style: concise, actionable, evidence-backed. Prefer bullet summaries with clear next steps.
- Risk: full_auto — no approval gate; use checkpoints, audit, and rollback instead.
"""

_DEFAULT_MEMORY = """# Company memory (ATRIUM)

- ATRIUM is a local, owner-operated AI company orchestrator.
- The Executive is the control plane: departments, tasks, tools, memory, schedules, and config.
- Compaction creates summaries only; raw transcripts stay in the append-only ledger.
"""


def company_memory_dir(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    path = settings.workspace_dir / "company"
    path.mkdir(parents=True, exist_ok=True)
    return path


def owner_profile_path(settings: Settings | None = None) -> Path:
    return company_memory_dir(settings) / USER_FILENAME


def company_memory_path(settings: Settings | None = None) -> Path:
    return company_memory_dir(settings) / MEMORY_FILENAME


def ensure_company_memory_files(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    user_path = owner_profile_path(settings)
    memory_path = company_memory_path(settings)
    if not user_path.exists():
        user_path.write_text(_DEFAULT_USER.strip() + "\n", encoding="utf-8")
    if not memory_path.exists():
        memory_path.write_text(_DEFAULT_MEMORY.strip() + "\n", encoding="utf-8")


def _read_optional(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def load_owner_profile(settings: Settings | None = None) -> str:
    ensure_company_memory_files(settings)
    return _read_optional(owner_profile_path(settings))


def load_company_memory(settings: Settings | None = None) -> str:
    ensure_company_memory_files(settings)
    return _read_optional(company_memory_path(settings))


def core_memory_blocks(settings: Settings | None = None) -> dict[str, str]:
    return {
        "human": load_owner_profile(settings),
        "company": load_company_memory(settings),
    }


def append_company_memory_entry(
    text: str,
    *,
    source: str = "system",
    confidence: float | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Append a source-backed note to MEMORY.md, returning the written metadata."""
    settings = settings or get_settings()
    ensure_company_memory_files(settings)
    clean = str(text or "").strip()
    if not clean:
        raise ValueError("company memory text is required")
    ts = now_ms()
    confidence_text = "" if confidence is None else f"; confidence={max(0.0, min(1.0, float(confidence))):.2f}"
    entry = (
        f"\n\n## {ts} - {source}\n\n"
        f"- source={source}{confidence_text}\n"
        f"- {clean}\n"
    )
    path = company_memory_path(settings)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(entry)
    return {"path": str(path), "ts": ts, "source": source, "confidence": confidence, "text": clean}


async def sync_company_memory_to_runtime(
    repo: Any,
    *,
    settings: Settings | None = None,
    labels: list[str] | None = None,
) -> dict[str, Any]:
    """Sync USER.md/MEMORY.md into ready runtime agents' core memory blocks."""
    settings = settings or get_settings()
    ensure_company_memory_files(settings)
    if not settings.use_letta_runtime:
        return {"ok": True, "enabled": False, "backend": settings.agent_backend, "updated": []}

    from ..runtime.factory import get_agent_runtime
    from ..runtime.letta_adapter import LettaRuntimeAdapter
    from ..runtime.provisioning import runtime_agent_key

    runtime = get_agent_runtime(settings)
    if not isinstance(runtime, LettaRuntimeAdapter):
        return {"ok": False, "enabled": True, "backend": settings.agent_backend, "updated": [], "error": "runtime is not Letta"}

    blocks = core_memory_blocks(settings)
    wanted = set(labels or blocks.keys())
    departments = await repo.list_departments()
    updated: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for dept in departments:
        meta = dept.get("runtime") if isinstance(dept.get("runtime"), dict) else {}
        runtime_id = meta.get("lettaAgentId")
        if not runtime_id:
            continue
        agent_key = str(meta.get("agentKey") or runtime_agent_key(dept))
        runtime._agent_ids[agent_key] = str(runtime_id)
        for label, value in blocks.items():
            if label not in wanted:
                continue
            try:
                result = await runtime.update_memory(agent_key, label=label, value=value)
                updated.append({
                    "departmentId": dept.get("id"),
                    "agentKey": agent_key,
                    "runtimeAgentId": runtime_id,
                    "label": label,
                    "ok": True,
                    "result": result,
                })
            except Exception as exc:
                errors.append({
                    "departmentId": str(dept.get("id") or ""),
                    "agentKey": agent_key,
                    "label": label,
                    "error": f"{type(exc).__name__}: {exc}",
                })
    return {
        "ok": not errors,
        "enabled": True,
        "backend": "letta",
        "labels": sorted(wanted),
        "updated": updated,
        "errors": errors,
    }
