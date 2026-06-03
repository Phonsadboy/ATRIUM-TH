"""Source-aware retrieval context for department memory."""
from __future__ import annotations

import re
from typing import Any

_TOKEN_RE = re.compile(r"[0-9A-Za-z\u0E00-\u0E7F]+")


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _tokens(value: Any) -> set[str]:
    return {
        token.lower()
        for token in _TOKEN_RE.findall(str(value or ""))
        if len(token) >= 3
    }


def _knowledge_terms(knowledge: list[dict[str, Any]]) -> set[str]:
    terms: set[str] = set()
    for item in knowledge:
        terms |= _tokens(item.get("id"))
        terms |= _tokens(item.get("title"))
        terms |= _tokens(item.get("text"))
        terms |= _tokens(item.get("source"))
        tags = item.get("tags") or []
        if isinstance(tags, list):
            terms |= set().union(*(_tokens(tag) for tag in tags)) if tags else set()
    return terms


def _node_terms(node: dict[str, Any]) -> set[str]:
    return _tokens(node.get("id")) | _tokens(node.get("label")) | _tokens(node.get("type"))


def _edge_from(edge: dict[str, Any]) -> str:
    return str(edge.get("from") or edge.get("from_id") or edge.get("from_") or "").strip()


def _edge_to(edge: dict[str, Any]) -> str:
    return str(edge.get("to") or edge.get("to_id") or "").strip()


def _source_label(item: dict[str, Any]) -> str:
    source = str(item.get("source") or "").strip()
    tags = [str(tag).strip() for tag in item.get("tags") or [] if str(tag).strip()]
    provenance = source or ", ".join(tag for tag in tags if ":" in tag)
    if provenance:
        return f" [source={_clip(provenance, 90)}]"
    return ""


def _temporal_label(item: dict[str, Any]) -> str:
    parts: list[str] = []
    source = str(item.get("source") or "").strip()
    if source:
        parts.append(f"source={_clip(source, 70)}")
    valid_from = item.get("validFrom", item.get("valid_from"))
    valid_to = item.get("validTo", item.get("valid_to"))
    if valid_from or valid_to:
        parts.append(f"valid={valid_from or '?'}..{valid_to or 'open'}")
    if item.get("confidence") is not None:
        try:
            parts.append(f"confidence={float(item.get('confidence')):.2f}")
        except (TypeError, ValueError):
            pass
    return f" [{'; '.join(parts)}]" if parts else ""


def select_relevant_graph(
    knowledge: list[dict[str, Any]],
    graph: dict[str, Any],
    *,
    max_nodes: int = 8,
    max_edges: int = 8,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Select graph nodes relevant to RAG hits, plus one-hop edges."""
    nodes = graph.get("nodes", []) if isinstance(graph, dict) else []
    edges = graph.get("edges", []) if isinstance(graph, dict) else []
    nodes = [node for node in nodes if isinstance(node, dict)]
    edges = [edge for edge in edges if isinstance(edge, dict)]
    if not nodes:
        return [], []

    knowledge_terms = _knowledge_terms(knowledge)
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for idx, node in enumerate(nodes):
        overlap = len(_node_terms(node) & knowledge_terms)
        scored.append((overlap, -idx, node))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)

    if knowledge_terms and scored and scored[0][0] > 0:
        selected_ids = {str(node.get("id")) for score, _, node in scored if score > 0}
    else:
        selected_ids = {str(node.get("id")) for _, _, node in scored[:max_nodes]}

    # Include one-hop neighbors so the graph context carries relations, not only
    # isolated nodes that happened to share words with a RAG hit.
    selected_edges: list[dict[str, Any]] = []
    for edge in edges:
        from_id = _edge_from(edge)
        to_id = _edge_to(edge)
        if from_id in selected_ids or to_id in selected_ids:
            selected_edges.append(edge)
            selected_ids.add(from_id)
            selected_ids.add(to_id)
        if len(selected_edges) >= max_edges:
            break

    selected_nodes = [node for node in nodes if str(node.get("id")) in selected_ids][:max_nodes]
    return selected_nodes, selected_edges[:max_edges]


def build_retrieval_context(
    knowledge: list[dict[str, Any]],
    graph: dict[str, Any],
    *,
    working_memory: str = "",
    max_knowledge: int = 5,
    max_nodes: int = 8,
    max_edges: int = 8,
) -> str:
    parts: list[str] = []
    if working_memory:
        parts.append(working_memory)
    if knowledge:
        lines = []
        for item in knowledge[:max_knowledge]:
            title = item.get("title") or item.get("id")
            lines.append(f"- {title}{_source_label(item)}: {_clip(item.get('text'), 420)}")
        parts.append("Department knowledge:\n" + "\n".join(lines))

    nodes, edges = select_relevant_graph(knowledge, graph, max_nodes=max_nodes, max_edges=max_edges)
    if nodes:
        labels = ", ".join(
            f"{node.get('label')}({node.get('type')}){_temporal_label(node)}"
            for node in nodes
            if node.get("label")
        )
        rels = ", ".join(
            f"{_edge_from(edge)}->{_edge_to(edge)}:{edge.get('rel')}{_temporal_label(edge)}"
            for edge in edges
        )
        parts.append("Relevant graph traversal:\n" + labels + (f"\nRelations: {rels}" if rels else ""))
    return "\n\n".join(parts)
