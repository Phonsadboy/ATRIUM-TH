"""Normalize provider-extracted memory before persistence."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

ALLOWED_NODE_TYPES = {"concept", "entity", "task", "person", "artifact"}


def _clip_text(value: Any, limit: int) -> str:
    text = str(value or "").replace("\r\n", "\n").strip()
    return text if len(text) <= limit else text[: limit - 1] + "..."


def _number(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if hi <= 1 and number > 1:
        number = number / 100
    return min(hi, max(lo, number))


def _int_ms(value: Any, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _choice(value: Any, allowed: set[str], default: str) -> str:
    return value if isinstance(value, str) and value in allowed else default


def _json_list(data: dict[str, Any], key: str) -> list[Any]:
    value = data.get(key)
    return value if isinstance(value, list) else []


def compaction_provenance_tags(department_id: str, archive_id: str, thread_id: str | None) -> list[str]:
    """Build audit tags for knowledge extracted from a compaction archive."""
    tags = ["compact", f"dept:{department_id}", f"archive:{archive_id}"]
    if thread_id:
        tags.append(f"thread:{thread_id}")
    return [_clip_text(tag, 40) for tag in tags]


def merge_memory_tags(*groups: list[str]) -> list[str]:
    """Deduplicate tags while preserving order and compact audit tags."""
    out: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for raw in group:
            tag = _clip_text(raw, 40)
            if tag and tag not in seen:
                seen.add(tag)
                out.append(tag)
    return out[:12]


def _source_from_item(item: dict[str, Any], *, archive_id: str, thread_id: str | None) -> str:
    source = _clip_text(item.get("source"), 160)
    if source:
        return source
    source = f"compact:{archive_id}"
    if thread_id:
        source = f"{source},thread:{thread_id}"
    return _clip_text(source, 160)


def normalize_compaction_extraction(
    data: dict[str, Any],
    *,
    department_id: str,
    archive_id: str,
    thread_id: str | None,
    now_ms: int,
    id_factory: Callable[[str], str],
    fallback_title: str,
    fallback_text: str,
) -> dict[str, list[dict[str, Any]]]:
    """Normalize compacted knowledge and graph facts into safe write models."""
    provenance_tags = compaction_provenance_tags(department_id, archive_id, thread_id)
    knowledge_items: list[dict[str, Any]] = []
    for item in _json_list(data, "knowledge")[:4]:
        if not isinstance(item, dict):
            continue
        text = _clip_text(item.get("text"), 2400)
        if not text:
            continue
        raw_tags = [str(tag) for tag in item.get("tags", []) if isinstance(tag, str)]
        knowledge_items.append({
            "id": id_factory("kn"),
            "title": _clip_text(item.get("title"), 120) or fallback_title,
            "ts": now_ms,
            "score": _number(item.get("score"), 0.82, 0, 1),
            "text": text,
            "tags": merge_memory_tags(raw_tags, provenance_tags),
        })
    if not knowledge_items:
        knowledge_items = [{
            "id": id_factory("kn"),
            "title": fallback_title,
            "ts": now_ms,
            "score": 0.82,
            "text": fallback_text,
            "tags": merge_memory_tags(["handoff"], provenance_tags),
        }]

    graph_nodes: list[dict[str, Any]] = []
    used_node_ids: set[str] = set()
    for item in _json_list(data, "graphNodes")[:8]:
        if not isinstance(item, dict):
            continue
        node_id = _clip_text(item.get("id"), 80) or id_factory("node")
        if node_id in used_node_ids:
            continue
        used_node_ids.add(node_id)
        graph_nodes.append({
            "id": node_id,
            "label": _clip_text(item.get("label"), 140) or node_id,
            "type": _choice(item.get("type"), ALLOWED_NODE_TYPES, "concept"),
            "validFrom": _int_ms(item.get("validFrom", item.get("valid_from")), now_ms),
            "validTo": _int_ms(item.get("validTo", item.get("valid_to"))),
            "confidence": _number(item.get("confidence"), 0.78, 0, 1),
            "source": _source_from_item(item, archive_id=archive_id, thread_id=thread_id),
        })
    if not graph_nodes:
        node_id = f"{department_id}_compact_{now_ms}"
        used_node_ids.add(node_id)
        graph_nodes.append({
            "id": node_id,
            "label": knowledge_items[0]["title"],
            "type": "concept",
            "validFrom": now_ms,
            "validTo": None,
            "confidence": 0.78,
            "source": _source_from_item({}, archive_id=archive_id, thread_id=thread_id),
        })

    graph_edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    for edge in _json_list(data, "graphEdges")[:10]:
        if not isinstance(edge, dict):
            continue
        from_id = _clip_text(edge.get("from"), 80)
        to_id = _clip_text(edge.get("to"), 80)
        rel = _clip_text(edge.get("rel"), 80)
        if not (from_id and to_id and rel):
            continue
        if from_id not in used_node_ids or to_id not in used_node_ids:
            continue
        key = (from_id, to_id, rel)
        if key in seen_edges:
            continue
        seen_edges.add(key)
        graph_edges.append({
            "from": from_id,
            "to": to_id,
            "rel": rel,
            "validFrom": _int_ms(edge.get("validFrom", edge.get("valid_from")), now_ms),
            "validTo": _int_ms(edge.get("validTo", edge.get("valid_to"))),
            "confidence": _number(edge.get("confidence"), 0.74, 0, 1),
            "source": _source_from_item(edge, archive_id=archive_id, thread_id=thread_id),
        })

    return {
        "knowledge": knowledge_items,
        "graphNodes": graph_nodes,
        "graphEdges": graph_edges,
    }
