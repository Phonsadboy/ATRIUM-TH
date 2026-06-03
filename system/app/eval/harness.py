"""Eval / telemetry harness scaffold (golden tasks + outcome metrics)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..clock import now_ms
from ..config import Settings, get_settings
from ..ids import uid
from ..provider.base import LLMMessage
from ..provider.registry import get_provider, provider_health
from .scoring import record_task_outcome

_JSON_BLOCK = re.compile(r"\{[\s\S]*\}")


@dataclass
class GoldenTask:
    id: str
    title: str
    prompt: str
    department_id: str = "executive"
    expected_tools: list[str] = field(default_factory=list)
    rubric: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "GoldenTask":
        return cls(
            id=str(raw.get("id") or raw.get("taskId") or "golden"),
            title=str(raw.get("title") or ""),
            prompt=str(raw.get("prompt") or ""),
            department_id=str(raw.get("departmentId") or "executive"),
            expected_tools=list(raw.get("expectedTools") or []),
            rubric=str(raw.get("rubric") or ""),
        )


@dataclass
class EvalOutcome:
    task_id: str
    success: bool
    revision_count: int = 0
    tools_used: list[str] = field(default_factory=list)
    score: float | None = None
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "taskId": self.task_id,
            "success": self.success,
            "revisionCount": self.revision_count,
            "toolsUsed": self.tools_used,
            "score": self.score,
            "notes": self.notes,
        }


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."


def _score(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, parsed))


class EvalHarness:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.tasks: list[GoldenTask] = []
        self._load_golden_tasks()

    def _load_golden_tasks(self) -> None:
        path = self.settings.eval_golden_tasks_file
        if not path or not path.is_file():
            return
        raw = json.loads(path.read_text(encoding="utf-8"))
        items = raw if isinstance(raw, list) else raw.get("tasks", [])
        self.tasks = [GoldenTask.from_dict(item) for item in items if isinstance(item, dict)]

    def list_tasks(self) -> list[dict[str, Any]]:
        return [
            {
                "id": t.id,
                "title": t.title,
                "prompt": t.prompt,
                "departmentId": t.department_id,
                "expectedTools": t.expected_tools,
                "rubric": t.rubric,
            }
            for t in self.tasks
        ]

    def get_task(self, task_id: str) -> GoldenTask | None:
        return next((task for task in self.tasks if task.id == task_id), None)

    def record_outcome(self, outcome: EvalOutcome) -> dict[str, Any]:
        return outcome.to_dict()

    def score_batch(self, outcomes: list[EvalOutcome]) -> dict[str, Any]:
        if not outcomes:
            return {"count": 0, "successRate": None, "avgRevisions": None}
        successes = sum(1 for o in outcomes if o.success)
        revisions = sum(o.revision_count for o in outcomes)
        scores = [o.score for o in outcomes if o.score is not None]
        return {
            "count": len(outcomes),
            "successRate": successes / len(outcomes),
            "avgRevisions": revisions / len(outcomes),
            "avgScore": (sum(scores) / len(scores)) if scores else None,
        }

    def status(self) -> dict[str, Any]:
        health = provider_health(self.settings)
        return {
            "enabled": self.settings.eval_harness_enabled,
            "goldenTaskCount": len(self.tasks),
            "goldenTasksPath": str(self.settings.eval_golden_tasks_file or ""),
            "judgeAvailable": bool(health.get("ready")),
            "judgeMode": "llm" if health.get("ready") else "unconfigured",
        }

    async def judge_response(
        self,
        task: GoldenTask,
        *,
        response_text: str,
        tools_used: list[str] | None = None,
        department: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        tools_used = [str(tool) for tool in (tools_used or []) if str(tool).strip()]
        missing_tools = [tool for tool in task.expected_tools if tool not in tools_used]
        dept = department or {}
        provider_id = str(dept.get("providerId") or "claude_code")
        model = str(dept.get("model") or "claude-sonnet-4-7")
        speed = str(dept.get("speed") or "standard")
        system = (
            "You are ATRIUM's eval judge. Grade one response to one golden task. "
            "Return JSON only with keys: score (0..1), passed (boolean), notes "
            "(short string), rubricFindings (array of strings), risks (array of strings). "
            "Do not reward fabricated live state. If required tool evidence is missing, fail."
        )
        user = {
            "goldenTask": {
                "id": task.id,
                "title": task.title,
                "prompt": task.prompt,
                "rubric": task.rubric,
                "expectedTools": task.expected_tools,
            },
            "observed": {
                "toolsUsed": tools_used,
                "missingTools": missing_tools,
                "response": _clip(response_text, 12000),
            },
        }
        raw_text = ""
        data: dict[str, Any] = {}
        error = ""
        try:
            provider = get_provider(provider_id, self.settings)
            result = await provider.complete(
                system=system,
                messages=[LLMMessage(role="user", content=json.dumps(user, ensure_ascii=False))],
                model=model,
                effort="medium",
                speed=speed,
            )
            raw_text = result.text or ""
            match = _JSON_BLOCK.search(raw_text)
            if not match:
                raise ValueError("judge did not return JSON")
            parsed = json.loads(match.group())
            if not isinstance(parsed, dict):
                raise ValueError("judge JSON was not an object")
            data = parsed
        except Exception as exc:
            error = str(exc)[:1000]
        score = _score(data.get("score"))
        passed = bool(data.get("passed")) and score >= 0.65 and not missing_tools and not error
        if missing_tools:
            score = min(score, 0.4)
        if error:
            score = 0.0
        return {
            "goldenTaskId": task.id,
            "passed": passed,
            "score": round(score, 4),
            "notes": _clip(data.get("notes") or error or "judge completed", 1200),
            "rubricFindings": data.get("rubricFindings") if isinstance(data.get("rubricFindings"), list) else [],
            "risks": data.get("risks") if isinstance(data.get("risks"), list) else [],
            "expectedTools": task.expected_tools,
            "toolsUsed": tools_used,
            "missingTools": missing_tools,
            "judgeMode": "llm" if not error else "error",
            "judgeProviderId": provider_id,
            "judgeModel": model,
            "rawJudge": _clip(raw_text, 4000),
            "error": error,
        }

    async def judge_and_record(
        self,
        repo: Any,
        *,
        task_id: str,
        response_text: str,
        tools_used: list[str] | None = None,
        department_id: str | None = None,
        actor: str = "eval_harness",
    ) -> dict[str, Any]:
        if not self.settings.eval_harness_enabled:
            raise ValueError("eval harness is disabled")
        task = self.get_task(task_id)
        if not task:
            raise KeyError(f"golden task not found: {task_id}")
        dept_id = department_id or task.department_id
        department = await repo.get_department(dept_id)
        judge = await self.judge_response(
            task,
            response_text=response_text,
            tools_used=tools_used,
            department=department,
        )
        now = now_ms()
        row = {
            "id": uid("golden"),
            "goldenTaskId": task.id,
            "departmentId": dept_id,
            "actor": actor,
            "prompt": task.prompt,
            "rubric": task.rubric,
            "responsePreview": _clip(response_text, 4000),
            "judge": judge,
            "passed": judge["passed"],
            "score": judge["score"],
            "toolsUsed": judge["toolsUsed"],
            "expectedTools": judge["expectedTools"],
            "missingTools": judge["missingTools"],
            "ts": now,
        }
        await repo.put_entity(
            "eval_golden_outcome",
            row,
            dept=dept_id,
            status="passed" if judge["passed"] else "failed",
            ts=now,
        )
        eval_row = await record_task_outcome(
            repo,
            task_id=f"golden:{task.id}",
            department_id=dept_id,
            outcome="done" if judge["passed"] else "revising",
            revision_count=0 if judge["passed"] else 1,
            accepted=bool(judge["passed"]),
            detail=f"golden task {task.id}: {judge['notes']}",
        )
        row["evalOutcomeId"] = eval_row["id"]
        await repo.put_entity(
            "eval_golden_outcome",
            row,
            dept=dept_id,
            status="passed" if judge["passed"] else "failed",
            ts=now,
        )
        await repo.add_activity(
            {
                "id": uid("ev"),
                "ts": now,
                "type": "system",
                "departmentId": dept_id,
                "text": (
                    f"eval golden task {task.id}: "
                    f"{'passed' if judge['passed'] else 'failed'} score={judge['score']}"
                ),
                "severity": "good" if judge["passed"] else "warn",
            }
        )
        return row
