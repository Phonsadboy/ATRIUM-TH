"""Capability registry — department charters as structured capabilities."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..clock import now_ms
from ..threads import is_exec

_TOKEN_RE = re.compile(r"[0-9A-Za-z\u0E00-\u0E7F]+")
CAPABILITY_ENTITY_TYPE = "org_capability"
DERIVED_CAPABILITY_SOURCE = "department_profile"


@dataclass
class DepartmentCapability:
    department_id: str
    name: str
    role: str
    charter: str
    skills: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    keywords: set[str] = field(default_factory=set)
    entity_ids: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    confidence: float = 0.65
    structured: bool = False

    def merge(self, other: "DepartmentCapability") -> None:
        self.skills = _unique([*self.skills, *other.skills])
        self.tools = _unique([*self.tools, *other.tools])
        self.keywords |= other.keywords
        self.entity_ids = _unique([*self.entity_ids, *other.entity_ids])
        self.sources = _unique([*self.sources, *other.sources])
        self.confidence = max(self.confidence, other.confidence)
        self.structured = self.structured or other.structured
        if other.structured and other.charter:
            self.charter = other.charter
        if other.structured and other.name:
            self.name = other.name

    def to_dict(self) -> dict[str, Any]:
        return {
            "departmentId": self.department_id,
            "name": self.name,
            "role": self.role,
            "charter": self.charter,
            "skills": self.skills,
            "tools": self.tools,
            "keywords": sorted(self.keywords),
            "entityIds": self.entity_ids,
            "sources": self.sources,
            "confidence": self.confidence,
            "structured": self.structured,
        }


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text) if len(t) >= 2}


def _unique(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return _unique([
            *(value.get(key) for key in ("name", "title", "label", "id") if value.get(key)),
            *(_strings(value.get("keywords"))),
        ])
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            out.extend(_strings(item))
        return _unique(out)
    return [str(value)]


def _safe_entity_part(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z_:-]+", "_", value.strip())
    cleaned = cleaned.strip("_")
    return cleaned[:80] or "department"


def capability_entity_id(department_id: str) -> str:
    return f"orgcap_{_safe_entity_part(department_id)}_profile"


def _department_keywords(dept: dict[str, Any]) -> set[str]:
    skills = _strings(dept.get("skills"))
    tools = _strings(dept.get("tools"))
    return _tokens(" ".join([
        str(dept.get("name") or ""),
        str(dept.get("role") or ""),
        str(dept.get("charter") or ""),
        *skills,
        *tools,
    ]))


def department_capability_entity(dept: dict[str, Any], *, updated_at: int | None = None) -> dict[str, Any]:
    updated_at = updated_at or now_ms()
    skills = _strings(dept.get("skills"))
    tools = _strings(dept.get("tools"))
    keywords = sorted(_department_keywords(dept))
    return {
        "id": capability_entity_id(str(dept.get("id") or "")),
        "departmentId": str(dept.get("id") or ""),
        "name": str(dept.get("name") or dept.get("id") or ""),
        "role": str(dept.get("role") or ""),
        "charter": str(dept.get("charter") or dept.get("role") or ""),
        "skills": skills,
        "tools": tools,
        "keywords": keywords,
        "source": DERIVED_CAPABILITY_SOURCE,
        "sourceFields": ["name", "role", "charter", "skills", "tools"],
        "confidence": 0.72,
        "status": "active",
        "updatedAt": updated_at,
    }


def _capability_from_department(dept: dict[str, Any]) -> DepartmentCapability:
    charter = str(dept.get("charter") or dept.get("role") or "")
    skills = _strings(dept.get("skills"))
    tools = _strings(dept.get("tools"))
    keywords = _department_keywords(dept)
    return DepartmentCapability(
        department_id=dept["id"],
        name=str(dept.get("name") or dept["id"]),
        role=str(dept.get("role") or ""),
        charter=charter,
        skills=skills,
        tools=tools,
        keywords=keywords,
        sources=["department_fallback"],
        confidence=0.6,
        structured=False,
    )


def _capability_from_entity(entity: dict[str, Any], dept: dict[str, Any]) -> DepartmentCapability | None:
    status = str(entity.get("status") or "active")
    if status in {"inactive", "retired", "deleted"} or entity.get("active") is False:
        return None
    dept_id = str(entity.get("departmentId") or entity.get("deptId") or dept.get("id") or "")
    if not dept_id:
        return None
    skills = _unique([*_strings(entity.get("skills")), *_strings(entity.get("capabilities"))])
    tools = _strings(entity.get("tools"))
    explicit_keywords = _strings(entity.get("keywords"))
    text = " ".join([
        str(entity.get("name") or ""),
        str(entity.get("title") or ""),
        str(entity.get("description") or ""),
        str(entity.get("charter") or ""),
        str(entity.get("role") or ""),
        *skills,
        *tools,
        *explicit_keywords,
    ])
    keywords = _tokens(text) | {kw.lower() for kw in explicit_keywords if len(kw) >= 2}
    try:
        confidence = float(entity.get("confidence", 0.8))
    except (TypeError, ValueError):
        confidence = 0.8
    return DepartmentCapability(
        department_id=dept_id,
        name=str(entity.get("name") or dept.get("name") or dept_id),
        role=str(entity.get("role") or dept.get("role") or ""),
        charter=str(entity.get("charter") or entity.get("description") or dept.get("charter") or ""),
        skills=skills,
        tools=tools,
        keywords=keywords,
        entity_ids=[str(entity.get("id") or "")] if entity.get("id") else [],
        sources=[str(entity.get("source") or "manual")],
        confidence=max(0.0, min(1.0, confidence)),
        structured=True,
    )


class CapabilityRegistry:
    def __init__(self, capabilities: list[DepartmentCapability] | None = None):
        self._by_id: dict[str, DepartmentCapability] = {}
        self._structured_entity_count = 0
        for cap in capabilities or []:
            if cap.structured:
                self._structured_entity_count += len(cap.entity_ids) or 1
            existing = self._by_id.get(cap.department_id)
            if existing:
                existing.merge(cap)
            else:
                self._by_id[cap.department_id] = cap

    @classmethod
    def from_departments(
        cls,
        departments: list[dict[str, Any]],
        capability_entities: list[dict[str, Any]] | None = None,
    ) -> "CapabilityRegistry":
        caps: list[DepartmentCapability] = []
        dept_map = {str(dept.get("id")): dept for dept in departments if dept.get("id")}
        for dept in departments:
            if is_exec(dept.get("id", "")):
                continue
            caps.append(_capability_from_department(dept))
        for entity in capability_entities or []:
            dept_id = str(entity.get("departmentId") or entity.get("deptId") or "")
            dept = dept_map.get(dept_id)
            if not dept or is_exec(dept_id):
                continue
            cap = _capability_from_entity(entity, dept)
            if cap:
                caps.append(cap)
        return cls(caps)

    def list(self) -> list[DepartmentCapability]:
        return list(self._by_id.values())

    def get(self, department_id: str) -> DepartmentCapability | None:
        return self._by_id.get(department_id)

    def score_text(self, department_id: str, text: str) -> float:
        cap = self._by_id.get(department_id)
        if not cap:
            return 0.0
        query = _tokens(text)
        if not query:
            return 0.0
        overlap = len(query & cap.keywords)
        if overlap == 0:
            return 0.0
        return overlap / max(len(query), 1)

    def rank_for_text(self, text: str, *, limit: int = 5) -> list[tuple[str, float]]:
        scored = [(cap.department_id, self.score_text(cap.department_id, text)) for cap in self._by_id.values()]
        scored.sort(key=lambda item: item[1], reverse=True)
        return [item for item in scored if item[1] > 0][:limit]

    def catalog(self) -> list[dict[str, Any]]:
        return [cap.to_dict() for cap in self._by_id.values()]

    def stats(self) -> dict[str, Any]:
        return {
            "departmentCount": len(self._by_id),
            "capabilityCount": len(self._by_id),
            "structuredEntityCount": self._structured_entity_count,
        }


def _same_capability_payload(existing: dict[str, Any], candidate: dict[str, Any]) -> bool:
    keys = (
        "departmentId",
        "name",
        "role",
        "charter",
        "skills",
        "tools",
        "keywords",
        "source",
        "sourceFields",
        "confidence",
        "status",
    )
    return all(existing.get(key) == candidate.get(key) for key in keys)


async def sync_department_capabilities(
    repo: Any,
    dept: dict[str, Any],
    *,
    source: str = DERIVED_CAPABILITY_SOURCE,
) -> dict[str, Any] | None:
    if is_exec(str(dept.get("id") or "")):
        return None
    entity = department_capability_entity(dept)
    entity["source"] = source
    existing = None
    if hasattr(repo, "get_entity"):
        existing = await repo.get_entity(CAPABILITY_ENTITY_TYPE, entity["id"])
    if isinstance(existing, dict) and _same_capability_payload(existing, entity):
        return existing
    if isinstance(existing, dict) and existing.get("createdAt"):
        entity["createdAt"] = existing["createdAt"]
    else:
        entity["createdAt"] = entity["updatedAt"]
    if hasattr(repo, "put_entity"):
        return await repo.put_entity(
            CAPABILITY_ENTITY_TYPE,
            entity,
            dept=entity["departmentId"],
            status="active",
            ts=entity["updatedAt"],
        )
    return entity


async def sync_all_department_capabilities(repo: Any) -> dict[str, Any]:
    synced: list[str] = []
    skipped: list[str] = []
    for dept in await repo.list_departments():
        if is_exec(str(dept.get("id") or "")):
            skipped.append(str(dept.get("id")))
            continue
        entity = await sync_department_capabilities(repo, dept)
        if entity:
            synced.append(str(entity.get("id")))
    return {"synced": synced, "skipped": skipped, "count": len(synced)}


async def deactivate_department_capabilities(
    repo: Any,
    department_id: str,
    *,
    reason: str,
    actor: str = "system",
) -> dict[str, Any]:
    now = now_ms()
    rows: list[dict[str, Any]] = []
    if hasattr(repo, "list_entities"):
        rows = await repo.list_entities(CAPABILITY_ENTITY_TYPE, dept=department_id, limit=2000)
    inactive: list[str] = []
    for row in rows:
        if str(row.get("status") or "active") in {"inactive", "retired", "deleted"}:
            continue
        row = dict(row)
        row.update({
            "status": "inactive",
            "inactiveAt": now,
            "inactiveReason": reason,
            "inactiveBy": actor,
            "updatedAt": now,
        })
        if hasattr(repo, "put_entity"):
            await repo.put_entity(CAPABILITY_ENTITY_TYPE, row, dept=department_id, status="inactive", ts=now)
        inactive.append(str(row.get("id")))
    return {"departmentId": department_id, "inactive": inactive}


async def build_capability_registry(repo: Any) -> CapabilityRegistry:
    departments = await repo.list_departments()
    capability_entities: list[dict[str, Any]] = []
    if hasattr(repo, "list_entities"):
        try:
            capability_entities = await repo.list_entities(CAPABILITY_ENTITY_TYPE, status="active", limit=2000)
        except TypeError:
            capability_entities = await repo.list_entities(CAPABILITY_ENTITY_TYPE, limit=2000)
    return CapabilityRegistry.from_departments(departments, capability_entities)
