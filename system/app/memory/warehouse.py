"""Knowledge warehouse — import with provenance and hybrid retrieval helpers."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from ..clock import now_ms
from ..config import get_settings
from ..ids import uid
from ..memory.embeddings import embedding_metadata, resolve_embedder


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."


async def import_text_source(
    repo: Any,
    *,
    department_id: str,
    title: str,
    text: str,
    source_uri: str,
    source_kind: str = "import",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    now = now_ms()
    entry_id = uid("wh")
    warehouse = {
        "id": entry_id,
        "departmentId": department_id,
        "title": title,
        "text": text,
        "sourceUri": source_uri,
        "sourceKind": source_kind,
        "tags": tags or [source_kind, f"dept:{department_id}"],
        "importedAt": now,
        "validFrom": now,
        "validTo": None,
    }
    await repo.put_entity("knowledge_warehouse", warehouse, dept=department_id, status=source_kind, ts=now)
    embedder = await resolve_embedder(settings)
    vec = (await embedder.embed([text[:8000]]))[0]
    kn = {
        "id": uid("kn"),
        "title": title,
        "ts": now,
        "score": 0.75,
        "text": _clip(text, 12000),
        "tags": ["warehouse", source_kind, *(tags or [])],
        "source": f"warehouse:{entry_id}",
    }
    await repo.add_knowledge(
        department_id,
        kn,
        embedding=vec,
        source=f"warehouse:{entry_id}",
        embedding_meta=embedding_metadata(embedder, vec),
    )
    await repo.refresh_department_memory_stats(department_id)
    return warehouse


async def import_local_file(
    repo: Any,
    *,
    department_id: str,
    path: Path,
    title: str | None = None,
) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(str(resolved))
    text = resolved.read_text(encoding="utf-8", errors="ignore")
    return await import_text_source(
        repo,
        department_id=department_id,
        title=title or resolved.name,
        text=text,
        source_uri=str(resolved),
        source_kind="file",
        tags=["file", resolved.suffix.lstrip(".") or "text"],
    )


async def import_url_snapshot(
    repo: Any,
    *,
    department_id: str,
    url: str,
    title: str | None = None,
    timeout_s: float = 20.0,
) -> dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("url must be http(s)")
    async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        text = resp.text
    return await import_text_source(
        repo,
        department_id=department_id,
        title=title or parsed.netloc,
        text=_clip(text, 50000),
        source_uri=url,
        source_kind="web",
        tags=["web", parsed.netloc],
    )


async def hybrid_search(
    repo: Any,
    department_id: str,
    query: str,
    *,
    k: int = 8,
) -> list[dict[str, Any]]:
    """Vector search + warehouse metadata merge."""
    settings = get_settings()
    embedder = await resolve_embedder(settings)
    vec = (await embedder.embed([query]))[0]
    knowledge = await repo.search_knowledge(department_id, vec, k=k)
    warehouse = [
        row
        for row in await repo.list_entities("knowledge_warehouse", dept=department_id, limit=k * 2)
        if query.lower() in str(row.get("title", "")).lower()
        or query.lower() in str(row.get("text", "")).lower()[:500]
    ][:k]
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in knowledge:
        key = str(item.get("id"))
        if key in seen:
            continue
        seen.add(key)
        out.append({**item, "retrievalBackend": "vector"})
    for item in warehouse:
        key = str(item.get("id"))
        if key in seen:
            continue
        seen.add(key)
        out.append({**item, "retrievalBackend": "warehouse_fts"})
    return out[:k]
