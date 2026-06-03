"""Company-wide retrieval for the Executive (cross-department namespace)."""
from __future__ import annotations

from typing import Any

from ..config import Settings, get_settings
from ..threads import EXEC_ID, EXEC_THREAD, is_exec
from .company_memory import load_company_memory, load_owner_profile
from .embeddings import resolve_embedder
from .retrieval import build_retrieval_context


async def executive_retrieval_context(
    repo: Any,
    query_text: str,
    *,
    settings: Settings | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """RAG + graph across all departments plus USER.md / MEMORY.md."""
    settings = settings or get_settings()
    text = str(query_text or "").strip()
    embedder = await resolve_embedder(settings)
    query_vec: list[float] | None = None
    if text:
        vecs = await embedder.embed([text])
        query_vec = vecs[0] if vecs else None

    departments = await repo.list_departments()
    dept_count = max(len(departments), 1)
    per_dept_k = max(2, (settings.rag_top_k * 2) // dept_count)

    hits: list[dict[str, Any]] = []
    for dept in departments:
        dept_id = str(dept.get("id") or "")
        if not dept_id:
            continue
        if query_vec:
            rows = await repo.search_knowledge(dept_id, query_vec, k=per_dept_k)
        else:
            rows = []
        if not rows:
            rows = await repo.list_knowledge(dept_id, limit=per_dept_k)
        for row in rows:
            item = dict(row)
            item["_departmentId"] = dept_id
            item["_departmentName"] = dept.get("name")
            hits.append(item)

    hits.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
    hits = hits[: settings.rag_top_k * 2]

    exec_dept = await repo.get_department(EXEC_ID)
    working_memory = ""
    if exec_dept:
        memory = exec_dept.get("memory") or {}
        summary = str(memory.get("workingSummary") or "").strip()
        if summary:
            provenance = []
            if memory.get("workingArchiveId"):
                provenance.append(f"archive={memory['workingArchiveId']}")
            if memory.get("workingThreadId"):
                provenance.append(f"thread={memory['workingThreadId']}")
            label = "Executive working memory"
            if provenance:
                label = f"{label} ({', '.join(provenance)})"
            working_memory = f"{label}:\n{summary}"

    graph = await repo.graph(EXEC_ID) if exec_dept else {"nodes": [], "edges": []}
    rag_context = build_retrieval_context(hits, graph, working_memory=working_memory)

    owner = load_owner_profile(settings)
    company = load_company_memory(settings)
    parts: list[str] = []
    if owner:
        parts.append("Owner profile (USER.md):\n" + owner[:4000])
    if company:
        parts.append("Company memory (MEMORY.md):\n" + company[:4000])
    if rag_context:
        parts.append("Company-wide retrieval:\n" + rag_context)

    cross_thread = await _recent_company_thread_notes(repo, settings)
    if cross_thread:
        parts.append(cross_thread)

    return "\n\n".join(parts), hits


async def _recent_company_thread_notes(repo: Any, settings: Settings) -> str:
    """Lightweight context from the executive room only."""
    lines: list[str] = []
    departments = {str(dept.get("id") or ""): dept for dept in await repo.list_departments()}
    messages = await repo.thread_messages(EXEC_THREAD, limit=max(6, settings.rag_top_k * 3))
    for msg in messages[-settings.rag_top_k * 2:]:
        participant = msg.get("participant") if isinstance(msg.get("participant"), dict) else {}
        dept_id = str(msg.get("departmentId") or participant.get("departmentId") or "")
        dept = departments.get(dept_id) or {}
        author = msg.get("authorName") or msg.get("role")
        text = str(msg.get("text") or "").replace("\n", " ")[:200]
        label = dept.get("name") or dept_id or "company"
        lines.append(f"- [{label} / {EXEC_THREAD}] {author}: {text}")
    if not lines:
        return ""
    return "Recent executive room chat:\n" + "\n".join(lines[: settings.rag_top_k * 2])


async def retrieval_context_for_department(
    repo: Any,
    dept: dict[str, Any],
    query_text: str,
) -> tuple[str, list[dict[str, Any]]]:
    if is_exec(dept.get("id", "")):
        return await executive_retrieval_context(repo, query_text)
    settings = get_settings()

    text = str(query_text or "").strip()
    vecs = await (await resolve_embedder(settings)).embed([text]) if text else []
    knowledge = await repo.search_knowledge(dept["id"], vecs[0], k=settings.rag_top_k) if vecs else []
    if not knowledge:
        knowledge = await repo.list_knowledge(dept["id"], limit=min(settings.rag_top_k, 5))
    graph = await repo.graph(dept["id"])
    memory = dept.get("memory") or {}
    working = ""
    if memory.get("workingSummary"):
        working = f"Working memory:\n{memory['workingSummary']}"
    return build_retrieval_context(knowledge, graph, working_memory=working), knowledge
