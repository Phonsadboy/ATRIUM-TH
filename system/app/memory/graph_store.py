"""Optional Kùzu graph mirror.

The relational tables remain the UI-facing source because they work in the
zero-setup SQLite path. When Kùzu is installed (`uv run --extra graph ...`) and
`ATRIUM_GRAPH_BACKEND=auto|kuzu`, graph writes are mirrored into an embedded
Kùzu database under `data/kuzu`, matching the architecture plan without making
the default developer boot path fragile.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Settings, get_settings

_RETRY_AFTER_S = 5.0


@dataclass
class GraphHealth:
    configured: str
    backend: str
    enabled: bool
    path: str | None = None
    error: str | None = None

    def dump(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "backend": self.backend,
            "enabled": self.enabled,
            "path": self.path,
            "error": self.error,
        }


class KuzuGraphMirror:
    def __init__(self, settings: Settings):
        self.configured = settings.graph_backend
        self.required = settings.graph_backend == "kuzu"
        self.enabled = False
        self.path: Path | None = None
        self.last_error: str | None = None
        self.started_at = time.monotonic()
        self._conn = None

        if settings.graph_backend == "relational":
            return

        try:
            import kuzu  # type: ignore
        except ImportError as exc:
            self.last_error = "kuzu package is not installed"
            if self.required:
                raise RuntimeError("ATRIUM_GRAPH_BACKEND=kuzu requires `uv run --extra graph ...`") from exc
            return

        try:
            self.path = (settings.data_dir / "kuzu").resolve()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            db = kuzu.Database(str(self.path))
            self._conn = kuzu.Connection(db)
            self._init_schema()
            self.enabled = True
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._conn = None
            if self.required:
                raise RuntimeError("ATRIUM_GRAPH_BACKEND=kuzu failed to initialize embedded Kùzu graph") from exc

    def health(self) -> GraphHealth:
        backend = "kuzu" if self.enabled else "relational"
        return GraphHealth(
            configured=self.configured,
            backend=backend,
            enabled=self.enabled,
            path=str(self.path) if self.path else None,
            error=self.last_error,
        )

    def should_retry(self) -> bool:
        if self.enabled or self.configured == "relational":
            return False
        if not self.last_error:
            return False
        if "not installed" in self.last_error.lower():
            return False
        return (time.monotonic() - self.started_at) >= _RETRY_AFTER_S

    def _execute(self, query: str, params: dict[str, Any] | None = None) -> None:
        if not self._conn:
            return
        try:
            self._conn.execute(query, params or {})
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            if self.required:
                raise
            self.enabled = False

    def _init_schema(self) -> None:
        # Kùzu may not support IF NOT EXISTS in older releases; duplicate-table
        # errors are harmless on restart, so auto mode records and continues.
        for query in [
            "CREATE NODE TABLE GraphNode(id STRING, dept STRING, label STRING, type STRING, x DOUBLE, y DOUBLE, PRIMARY KEY(id))",
            "CREATE REL TABLE GraphEdge(FROM GraphNode TO GraphNode, dept STRING, rel STRING)",
        ]:
            try:
                self._conn.execute(query)  # type: ignore[union-attr]
            except Exception as exc:
                msg = str(exc).lower()
                if "exist" not in msg and "already" not in msg:
                    raise

    def upsert_node(self, dept_id: str, node_id: str, label: str, ntype: str, x: float, y: float) -> None:
        if not self.enabled:
            return
        self._execute(
            """
            MERGE (n:GraphNode {id: $id})
            SET n.dept = $dept, n.label = $label, n.type = $type, n.x = $x, n.y = $y
            """,
            {"id": node_id, "dept": dept_id, "label": label, "type": ntype, "x": float(x), "y": float(y)},
        )

    def add_edge(self, dept_id: str, from_id: str, to_id: str, rel: str) -> None:
        if not self.enabled:
            return
        self._execute(
            """
            MATCH (a:GraphNode {id: $from_id}), (b:GraphNode {id: $to_id})
            CREATE (a)-[:GraphEdge {dept: $dept, rel: $rel}]->(b)
            """,
            {"from_id": from_id, "to_id": to_id, "dept": dept_id, "rel": rel},
        )

    def delete_department(self, dept_id: str) -> None:
        if not self.enabled:
            return
        self._execute("MATCH (n:GraphNode) WHERE n.dept = $dept DETACH DELETE n", {"dept": dept_id})


_mirror: KuzuGraphMirror | None = None


def init_graph_backend(settings: Settings | None = None) -> KuzuGraphMirror:
    global _mirror
    _mirror = KuzuGraphMirror(settings or get_settings())
    return _mirror


def get_graph_mirror(settings: Settings | None = None) -> KuzuGraphMirror:
    global _mirror
    if _mirror is None or _mirror.should_retry():
        _mirror = KuzuGraphMirror(settings or get_settings())
    return _mirror


def graph_health() -> dict[str, Any]:
    return get_graph_mirror().health().dump()


def reset_graph_backend() -> None:
    global _mirror
    _mirror = None
