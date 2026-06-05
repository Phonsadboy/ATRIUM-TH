"""Knowledge warehouse — import with provenance and hybrid retrieval helpers."""
from __future__ import annotations

import hashlib
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


def _warehouse_dedupe_key(*, department_id: str, source_kind: str, source_uri: str, text: str) -> str:
    h = hashlib.sha256()
    for part in (department_id, source_kind, source_uri, text):
        h.update(str(part or "").strip().encode("utf-8", errors="ignore"))
        h.update(b"\0")
    return h.hexdigest()


def _warehouse_source_text_match(
    item: dict[str, Any],
    *,
    department_id: str,
    source_kind: str,
    source_uri: str,
    text: str,
) -> bool:
    return (
        str(item.get("departmentId") or "").strip() == str(department_id or "").strip()
        and str(item.get("sourceKind") or "").strip() == str(source_kind or "").strip()
        and str(item.get("sourceUri") or "").strip() == str(source_uri or "").strip()
        and str(item.get("text") or "").strip() == str(text or "").strip()
    )


async def _existing_warehouse_entry(
    repo: Any,
    *,
    department_id: str,
    entry_id: str,
    dedupe_key: str,
    source_kind: str,
    source_uri: str,
    text: str,
) -> dict[str, Any] | None:
    get_entity = getattr(repo, "get_entity", None)
    if callable(get_entity):
        existing = await get_entity("knowledge_warehouse", entry_id)
        if isinstance(existing, dict):
            return existing
    list_entities = getattr(repo, "list_entities", None)
    if callable(list_entities):
        for item in await list_entities("knowledge_warehouse", dept=department_id, limit=1000):
            if not isinstance(item, dict):
                continue
            if item.get("dedupeKey") == dedupe_key or _warehouse_source_text_match(
                item,
                department_id=department_id,
                source_kind=source_kind,
                source_uri=source_uri,
                text=text,
            ):
                return item
    return None


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
    dedupe_key = _warehouse_dedupe_key(
        department_id=department_id,
        source_kind=source_kind,
        source_uri=source_uri,
        text=text,
    )
    entry_id = f"wh_{dedupe_key[:24]}"
    existing = await _existing_warehouse_entry(
        repo,
        department_id=department_id,
        entry_id=entry_id,
        dedupe_key=dedupe_key,
        source_kind=source_kind,
        source_uri=source_uri,
        text=text,
    )
    if existing:
        return {**existing, "deduped": True, "duplicateOf": existing.get("id") or entry_id}
    warehouse = {
        "id": entry_id,
        "departmentId": department_id,
        "title": title,
        "text": text,
        "sourceUri": source_uri,
        "sourceKind": source_kind,
        "dedupeKey": dedupe_key,
        "textHash": hashlib.sha256(str(text or "").encode("utf-8", errors="ignore")).hexdigest(),
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
