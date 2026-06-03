"""Deterministic knowledge-debt scoring helpers.

The API layer can expose these values as a dashboard, but the scoring rules
live with memory so they can be tested without a running provider or UI.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

STALE_AFTER_MS = 30 * 86_400_000
PROVENANCE_TAG_PREFIXES = (
    "archive:",
    "artifact:",
    "import:",
    "lesson:",
    "source:",
    "thread:",
)

_SPACE_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_-]{2,}")
_NEGATIVE_RE = re.compile(
    r"\b(do not|don't|never|avoid|disable|forbid|forbidden|must not|should not|no longer|deprecated)\b"
)
_POSITIVE_RE = re.compile(
    r"\b(use|uses|enable|allow|allowed|prefer|preferred|adopt|required|must use|should use)\b"
)
_SUBJECT_STOPWORDS = {
    "about",
    "adopt",
    "allow",
    "allowed",
    "avoid",
    "disable",
    "enable",
    "forbid",
    "forbidden",
    "must",
    "never",
    "not",
    "preferred",
    "required",
    "should",
    "the",
    "this",
    "use",
    "uses",
}


def _str(value: Any) -> str:
    return str(value or "").strip()


def _normalized(value: Any) -> str:
    return _SPACE_RE.sub(" ", _str(value).lower())


def _tags(entry: dict[str, Any]) -> list[str]:
    raw = entry.get("tags") or []
    if not isinstance(raw, list):
        return []
    return [_str(tag) for tag in raw if _str(tag)]


def has_provenance(entry: dict[str, Any]) -> bool:
    """Return whether a knowledge entry has auditable source context."""
    if _str(entry.get("source")):
        return True
    tags = _tags(entry)
    return any(tag.startswith(PROVENANCE_TAG_PREFIXES) for tag in tags)


def _stale_count(knowledge: Iterable[dict[str, Any]], now_ms: int, stale_after_ms: int) -> int:
    stale = 0
    for entry in knowledge:
        try:
            ts = int(entry.get("ts", now_ms))
        except (TypeError, ValueError):
            ts = now_ms
        if now_ms - ts > stale_after_ms:
            stale += 1
    return stale


def _duplicate_count(values: Iterable[str]) -> int:
    seen: set[str] = set()
    duplicates = 0
    for value in values:
        key = _normalized(value)
        if not key:
            continue
        if key in seen:
            duplicates += 1
        seen.add(key)
    return duplicates


def _knowledge_fingerprint(entry: dict[str, Any]) -> str:
    return _normalized(entry.get("title")) or _normalized(entry.get("text"))


def _polarity(entry: dict[str, Any]) -> int:
    text = _normalized(f"{entry.get('title', '')} {entry.get('text', '')}")
    if _NEGATIVE_RE.search(text):
        return -1
    if _POSITIVE_RE.search(text):
        return 1
    return 0


def _subject_tokens(entry: dict[str, Any]) -> set[str]:
    text = _normalized(f"{entry.get('title', '')} {entry.get('text', '')}")
    return {
        token
        for token in _TOKEN_RE.findall(text)
        if token not in _SUBJECT_STOPWORDS
    }


def _conflicting_count(knowledge: list[dict[str, Any]]) -> int:
    conflicts = 0
    normalized_entries = [
        (_polarity(entry), _subject_tokens(entry))
        for entry in knowledge
    ]
    for idx, (polarity, tokens) in enumerate(normalized_entries):
        if not polarity or not tokens:
            continue
        for other_polarity, other_tokens in normalized_entries[idx + 1:]:
            if polarity == other_polarity or not other_polarity or not other_tokens:
                continue
            overlap = tokens & other_tokens
            if len(overlap) >= 2:
                conflicts += 1
                continue
            union = tokens | other_tokens
            if union and len(overlap) / len(union) >= 0.45:
                conflicts += 1
    return conflicts


def compute_department_knowledge_debt(
    department_id: str,
    knowledge: list[dict[str, Any]],
    graph: dict[str, Any],
    now_ms: int,
    stale_after_ms: int = STALE_AFTER_MS,
) -> dict[str, Any]:
    """Score v0.4 section 19 knowledge health without provider calls."""
    nodes = graph.get("nodes", []) if isinstance(graph, dict) else []
    edges = graph.get("edges", []) if isinstance(graph, dict) else []
    if not isinstance(nodes, list):
        nodes = []
    if not isinstance(edges, list):
        edges = []

    stale = _stale_count(knowledge, now_ms, stale_after_ms)
    unsourced = sum(1 for entry in knowledge if not has_provenance(entry))

    node_ids = {
        _str(node.get("id"))
        for node in nodes
        if isinstance(node, dict) and _str(node.get("id"))
    }
    connected: set[str] = set()
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        from_id = _str(edge.get("from") or edge.get("from_id") or edge.get("from_"))
        to_id = _str(edge.get("to") or edge.get("to_id"))
        if from_id:
            connected.add(from_id)
        if to_id:
            connected.add(to_id)
    orphaned = sum(1 for node_id in node_ids if node_id not in connected)

    duplicate_nodes = _duplicate_count(
        _str(node.get("label")) for node in nodes if isinstance(node, dict)
    )
    duplicate_knowledge = _duplicate_count(_knowledge_fingerprint(entry) for entry in knowledge)
    duplicate = duplicate_nodes + duplicate_knowledge

    # LLM contradiction judging can layer in later. This deterministic baseline
    # catches obvious same-subject opposite-polarity memories without provider
    # calls, so the dashboard is useful in offline tests.
    conflicting = _conflicting_count(knowledge)

    debt = stale + unsourced + orphaned + duplicate + conflicting
    health = max(0.0, round(1.0 - min(debt, 25) / 25, 3))
    return {
        "departmentId": department_id,
        "stale": stale,
        "conflicting": conflicting,
        "unsourced": unsourced,
        "orphaned": orphaned,
        "duplicate": duplicate,
        "health": health,
    }
