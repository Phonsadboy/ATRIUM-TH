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
- Executive naming: in the first real conversation, ask what name the owner wants the executive to use for itself; when the owner answers, rename the executive before continuing.
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
    """Ensure USER.md/MEMORY.md exist for ATRIUM-native memory.

    Native runtime reads app-owned memory/RAG directly, so there is no external
    agent memory service to sync.
    """
    settings = settings or get_settings()
    ensure_company_memory_files(settings)
    blocks = core_memory_blocks(settings)
    wanted = set(labels or blocks.keys())
    del repo
    return {
        "ok": True,
        "enabled": True,
        "backend": "native",
        "externalRuntime": False,
        "labels": sorted(wanted),
        "updated": [],
        "errors": [],
    }
