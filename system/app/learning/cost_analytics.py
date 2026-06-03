"""Extended cost analytics — forecast/alert without blocking work."""
from __future__ import annotations

import json
import re
from typing import Any

from ..clock import DAY_MS, now_ms
from ..config import get_settings
from sqlalchemy import select

from ..db import tables as T

_KV_RE = re.compile(r"([A-Za-z][A-Za-z0-9_]*?)=([^;,\s]+)")


def _round_add(target: dict[str, float], key: str | None, value: float) -> None:
    bucket = str(key or "unknown").strip() or "unknown"
    target[bucket] = round(target.get(bucket, 0.0) + float(value or 0.0), 6)


def _detail_metadata(detail: str | None) -> dict[str, Any]:
    text = str(detail or "").strip()
    if not text:
        return {}
    candidate = text
    if candidate.startswith("json:"):
        candidate = candidate[5:].strip()
    if candidate.startswith("{"):
        try:
            parsed = json.loads(candidate)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    pairs = {key: value for key, value in _KV_RE.findall(text)}
    if pairs:
        if "skillId" in pairs and "skillIds" not in pairs:
            pairs["skillIds"] = [pairs["skillId"]]
        return pairs
    return {}


def _listify(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item or "").strip()]
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item or "").strip()]
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,|]", value) if item.strip()]
    if value:
        return [str(value)]
    return []


def _usage_type(row: T.CostRecordRow, meta: dict[str, Any]) -> str:
    explicit = str(meta.get("usageType") or meta.get("usage") or "").strip()
    if explicit:
        return explicit
    if row.category in {"memory", "tool"}:
        return row.category
    detail = str(row.detail or "").casefold()
    if any(term in detail for term in ("memory", "rag", "retrieval", "compaction", "embedding")):
        return "memory"
    if any(term in detail for term in ("tool", "call_atrium_api", "run_owner_tool")):
        return "tool"
    return "llm"


async def cost_analytics(repo: Any, *, dept_id: str | None = None) -> dict[str, Any]:
    settings = get_settings()
    base = await repo.cost_report("day" if not dept_id else "dept", dept_id)
    now = now_ms()
    today_start = now - (now % DAY_MS)
    window_start = today_start - 6 * DAY_MS

    q = select(T.CostRecordRow).where(T.CostRecordRow.ts >= window_start)
    if dept_id:
        q = q.where(T.CostRecordRow.department_id == dept_id)
    recs = (await repo.s.execute(q)).scalars().all()

    departments = {dept["id"]: dept for dept in await repo.list_departments()}
    by_provider: dict[str, float] = {}
    by_model: dict[str, float] = {}
    by_category: dict[str, float] = {}
    by_department: dict[str, float] = {}
    by_agent: dict[str, float] = {}
    by_project: dict[str, float] = {}
    by_skill: dict[str, float] = {}
    by_usage_type: dict[str, float] = {}
    by_tool: dict[str, float] = {}
    token_totals = {"tokensIn": 0, "tokensOut": 0}
    for row in recs:
        meta = _detail_metadata(row.detail)
        provider = row.provider_id or "unknown"
        model = row.model or "unknown"
        _round_add(by_provider, provider, row.usd)
        _round_add(by_model, model, row.usd)
        _round_add(by_category, row.category, row.usd)
        _round_add(by_department, row.department_id, row.usd)
        agent_id = str(meta.get("agentId") or meta.get("agent") or row.department_id)
        agent_name = str(meta.get("agentName") or departments.get(agent_id, {}).get("agentName") or agent_id)
        _round_add(by_agent, agent_name, row.usd)
        project_id = meta.get("projectId") or meta.get("project")
        if project_id:
            _round_add(by_project, str(project_id), row.usd)
        for skill_id in _listify(meta.get("skillIds") or meta.get("skillId") or meta.get("skill")):
            _round_add(by_skill, skill_id, row.usd)
        usage_type = _usage_type(row, meta)
        _round_add(by_usage_type, usage_type, row.usd)
        tool_name = meta.get("tool") or meta.get("toolName")
        if tool_name:
            _round_add(by_tool, str(tool_name), row.usd)
        token_totals["tokensIn"] += int(row.tokens_in or 0)
        token_totals["tokensOut"] += int(row.tokens_out or 0)

    cap = (await repo.get_company()).daily_cap_usd if await repo.get_company() else settings.daily_cap_usd
    forecast = float(base.get("forecastUsd") or 0)
    alerts: list[str] = list(base.get("anomalies") or [])
    if cap > 0 and forecast >= cap * settings.chat_budget_warning_ratio:
        alerts.append(
            f"forecast ${forecast:.2f} ใกล้/เกินเพดาน ${cap:.2f} — telemetry only, ไม่หยุดงานอัตโนมัติ"
        )

    return {
        **base,
        "byProvider": by_provider,
        "byModel": by_model,
        "byActivityCategory": by_category,
        "byDepartment": by_department,
        "byAgent": by_agent,
        "byProject": by_project,
        "bySkill": by_skill,
        "byUsageType": by_usage_type,
        "byTool": by_tool,
        "memoryToolUsage": {
            "memory": round(by_usage_type.get("memory", 0.0), 6),
            "tool": round(by_usage_type.get("tool", 0.0), 6),
        },
        "tokens": token_totals,
        "alerts": alerts,
        "blocking": False,
        "telemetryOnly": True,
    }
