"""Tool Foundry — design, test, register, version, rollback custom tools."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..clock import now_ms
from ..config import get_settings
from ..ids import uid
from .registry import RiskClass, ToolRegistry, ToolSpec

_TOOL_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{1,127}$")
_PYTHON_COMMAND_RISKS = {
    "safe_read": "command",
}
_VALID_RISKS = {
    "safe_read",
    "local_write",
    "host_write",
    "command",
    "network",
    "desktop",
    "credential",
    "external_send",
    "destructive",
    "privileged",
}


class _FormatArgs(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


@dataclass
class ToolDraft:
    name: str
    description: str
    risk_class: RiskClass
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    executor: str = "host"
    tests: list[Any] = field(default_factory=list)
    implementation: dict[str, Any] = field(default_factory=dict)
    version: int = 1

    def implementation_kind(self) -> str:
        return str(self.implementation.get("kind") or ("python" if self.implementation.get("source") else "template")).strip().lower()

    def effective_risk_class(self) -> RiskClass:
        risk = _PYTHON_COMMAND_RISKS.get(self.risk_class, self.risk_class) if self.implementation_kind() == "python" else self.risk_class
        return risk  # type: ignore[return-value]

    def to_spec(self) -> ToolSpec:
        risk = self.effective_risk_class()
        return ToolSpec(
            name=self.name,
            risk_class=risk,
            description=self.description,
            mutates_state=risk not in {"safe_read"},
            executor=self.executor,
            supports_checkpoint=risk not in {"safe_read"},
            rollback_capable=risk in {"local_write", "host_write", "command"},
            input_schema=self.input_schema,
            output_schema=self.output_schema,
        )

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ToolDraft":
        implementation = dict(raw.get("implementation") or {})
        if raw.get("source") and "source" not in implementation:
            implementation["source"] = raw.get("source")
            implementation.setdefault("kind", "python")
        if raw.get("template") and "template" not in implementation:
            implementation["template"] = raw.get("template")
            implementation.setdefault("kind", "template")
        return cls(
            name=str(raw["name"]),
            description=str(raw.get("description") or ""),
            risk_class=raw.get("riskClass") or raw.get("risk_class") or "safe_read",
            input_schema=dict(raw.get("inputSchema") or raw.get("input_schema") or {}),
            output_schema=dict(raw.get("outputSchema") or raw.get("output_schema") or {}),
            executor=str(raw.get("executor") or "host"),
            tests=list(raw.get("tests") or []),
            implementation=implementation,
            version=int(raw.get("version") or 1),
        )


class ToolFoundry:
    def __init__(self, registry: ToolRegistry):
        self.registry = registry

    def design(self, draft: ToolDraft) -> dict[str, Any]:
        return {
            "draftId": uid("tool-draft"),
            "name": draft.name,
            "riskClass": draft.risk_class,
            "executor": draft.executor,
            "inputSchema": draft.input_schema,
            "outputSchema": draft.output_schema,
            "tests": draft.tests,
            "implementation": self._public_implementation(draft),
            "createdAt": now_ms(),
        }

    def _public_implementation(self, draft: ToolDraft) -> dict[str, Any]:
        implementation = self._normalize_implementation(draft)
        if "source" in implementation:
            implementation = {**implementation, "sourceSha256": hashlib.sha256(str(implementation["source"]).encode("utf-8")).hexdigest()}
            implementation.pop("source", None)
        return implementation

    def _normalize_implementation(self, draft: ToolDraft) -> dict[str, Any]:
        raw = dict(draft.implementation or {})
        kind = str(raw.get("kind") or ("python" if raw.get("source") else "template")).strip().lower()
        implementation = {**raw, "kind": kind}
        if kind == "template":
            implementation["template"] = str(raw.get("template") or "")
        if kind == "python":
            implementation["source"] = str(raw.get("source") or "")
            implementation["entrypoint"] = str(raw.get("entrypoint") or "run")
        timeout = raw.get("timeoutSeconds", raw.get("timeout_seconds", 10))
        try:
            implementation["timeoutSeconds"] = max(1.0, min(float(timeout), 120.0))
        except (TypeError, ValueError):
            implementation["timeoutSeconds"] = 10.0
        return implementation

    def _wrapper_dir(self, draft: ToolDraft, version: int) -> Path:
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", draft.name).strip("._") or "custom_tool"
        return get_settings().workspace_dir / "company" / "tool_foundry" / safe_name / f"v{version}"

    def _python_wrapper_source(self, source: str, entrypoint: str) -> str:
        return f'''"""Generated ATRIUM Tool Foundry wrapper."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import traceback

USER_SOURCE = {source!r}
ENTRYPOINT = {entrypoint!r}


def _load_args() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        return {{}}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise TypeError("tool input must be a JSON object")
    return parsed


def _main() -> None:
    namespace: dict = {{}}
    try:
        exec(compile(USER_SOURCE, "<atrium-custom-tool>", "exec"), namespace)
        fn = namespace.get(ENTRYPOINT) or namespace.get("run") or namespace.get("main")
        if not callable(fn):
            raise RuntimeError("custom Python tool must define a callable run(args) function")
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            result = fn(_load_args())
        if result is None:
            result = {{}}
        if not isinstance(result, dict):
            result = {{"value": result}}
        stdout = captured.getvalue()
        if stdout:
            result.setdefault("stdout", stdout)
        sys.stdout.write(json.dumps(result, ensure_ascii=False, default=str))
    except Exception as exc:
        sys.stderr.write(json.dumps({{
            "error": f"{{type(exc).__name__}}: {{exc}}",
            "traceback": traceback.format_exc(limit=20),
        }}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    _main()
'''

    def _write_python_wrapper(self, path: Path, source: str, entrypoint: str) -> dict[str, Any]:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = self._python_wrapper_source(source, entrypoint)
        path.write_text(content, encoding="utf-8")
        return {
            "wrapperPath": str(path),
            "wrapperDigest": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }

    def _run_python_wrapper(self, path: Path, args: dict[str, Any], *, timeout: float, cwd: Path | None = None) -> dict[str, Any]:
        completed = subprocess.run(
            [sys.executable, str(path)],
            input=json.dumps(args, ensure_ascii=False),
            text=True,
            capture_output=True,
            cwd=cwd,
            timeout=timeout,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
            raise RuntimeError(f"custom tool failed: {detail[:2000]}")
        try:
            parsed = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"custom tool returned non-JSON output: {completed.stdout[:1000]}") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("custom tool output must be a JSON object")
        return parsed

    def _test_cases(self, draft: ToolDraft) -> list[dict[str, Any]]:
        cases: list[dict[str, Any]] = []
        for index, item in enumerate(draft.tests):
            if isinstance(item, dict):
                cases.append({
                    "name": str(item.get("name") or f"test_{index + 1}"),
                    "args": item.get("args") if isinstance(item.get("args"), dict) else {},
                    "expect": item.get("expect", item.get("expected")),
                    "contains": item.get("contains"),
                })
        return cases or [{"name": "smoke", "args": {}, "expect": None, "contains": None}]

    def _assert_expected(self, result: dict[str, Any], case: dict[str, Any]) -> list[str]:
        issues: list[str] = []
        expected = case.get("expect")
        if isinstance(expected, dict):
            for key, value in expected.items():
                if result.get(key) != value:
                    issues.append(f"{case['name']}: expected {key}={value!r}, got {result.get(key)!r}")
        contains = case.get("contains")
        if contains:
            rendered = json.dumps(result, ensure_ascii=False, sort_keys=True)
            needles = contains if isinstance(contains, list) else [contains]
            for needle in needles:
                if str(needle) not in rendered:
                    issues.append(f"{case['name']}: output does not contain {needle!r}")
        return issues

    async def register(
        self,
        repo: Any,
        draft: ToolDraft,
        *,
        actor: str = "executive",
        checkpoint_id: str | None = None,
    ) -> dict[str, Any]:
        spec = draft.to_spec()
        self.registry.register(spec)
        existing = await repo.get_entity("custom_tool", spec.name)
        version = int((existing or {}).get("version") or 0) + 1
        implementation = self._normalize_implementation(draft)
        wrapper: dict[str, Any] = {}
        if implementation["kind"] == "python":
            wrapper_path = self._wrapper_dir(draft, version) / "tool.py"
            wrapper = self._write_python_wrapper(
                wrapper_path,
                str(implementation["source"]),
                str(implementation.get("entrypoint") or "run"),
            )
        registered_at = now_ms()
        versions = list((existing or {}).get("versions") or [])
        version_snapshot = {
            "version": version,
            "registeredAt": registered_at,
            "actor": actor,
            "catalogRow": spec.to_catalog_row(),
            "implementation": implementation,
            **wrapper,
        }
        record = {
            "id": spec.name,
            "tool": spec.name,
            "version": version,
            "actor": actor,
            "registeredAt": registered_at,
            "status": "active",
            "catalogRow": spec.to_catalog_row(),
            "implementation": implementation,
            **wrapper,
            "checkpointId": checkpoint_id,
            "tests": draft.tests,
            "versions": [*versions, version_snapshot],
            "history": [
                *list((existing or {}).get("history") or []),
                {"event": "register", "version": version, "registeredAt": registered_at, "actor": actor},
            ],
        }
        await repo.put_entity("custom_tool", record, status="active", ts=record["registeredAt"])
        return record

    async def run_tests(self, draft: ToolDraft) -> dict[str, Any]:
        """Validate custom tool metadata and execute local wrapper smoke tests."""
        issues: list[str] = []
        implementation = self._normalize_implementation(draft)
        kind = implementation["kind"]
        if not _TOOL_NAME_RE.match(draft.name):
            issues.append("tool name must start with a letter and contain only letters, numbers, dots, underscores, or dashes")
        if self.registry.get(draft.name):
            issues.append("custom tool name cannot shadow a built-in tool")
        if draft.risk_class not in _VALID_RISKS:
            issues.append(f"invalid risk class: {draft.risk_class}")
        if not draft.description.strip():
            issues.append("description required")
        if kind not in {"template", "python"}:
            issues.append(f"unsupported implementation kind: {kind}")
        if kind == "template" and not str(implementation.get("template") or "").strip():
            issues.append("template implementation requires template")
        if kind == "python" and not str(implementation.get("source") or "").strip():
            issues.append("python implementation requires source")
        if kind == "python" and draft.executor not in {"host", "sandbox", "local"}:
            issues.append("python implementation executor must be host, local, or sandbox")
        test_results: list[dict[str, Any]] = []
        if not issues:
            try:
                if kind == "python":
                    source = str(implementation["source"])
                    compile(source, "<atrium-custom-tool>", "exec")
                    with tempfile.TemporaryDirectory(prefix="atrium_tool_foundry_") as tmp:
                        path = Path(tmp) / "tool.py"
                        self._write_python_wrapper(path, source, str(implementation.get("entrypoint") or "run"))
                        for case in self._test_cases(draft):
                            result = self._run_python_wrapper(path, dict(case.get("args") or {}), timeout=float(implementation["timeoutSeconds"]))
                            case_issues = self._assert_expected(result, case)
                            issues.extend(case_issues)
                            test_results.append({"name": case["name"], "ok": not case_issues, "result": result})
                if kind == "template":
                    template = str(implementation["template"])
                    for case in self._test_cases(draft):
                        args = dict(case.get("args") or {})
                        rendered = template.format_map(_FormatArgs(args))
                        result = {"rendered": rendered}
                        case_issues = self._assert_expected(result, case)
                        issues.extend(case_issues)
                        test_results.append({"name": case["name"], "ok": not case_issues, "result": result})
            except subprocess.TimeoutExpired as exc:
                issues.append(f"test timed out after {exc.timeout}s")
            except Exception as exc:
                issues.append(f"{type(exc).__name__}: {exc}")
        return {
            "ok": not issues,
            "tool": draft.name,
            "riskClass": draft.effective_risk_class(),
            "implementation": self._public_implementation(draft),
            "testsRun": len(test_results) + 4,
            "results": test_results,
            "issues": issues,
        }

    async def rollback(self, repo: Any, name: str, *, reason: str, actor: str = "executive") -> dict[str, Any]:
        record = await repo.get_entity("custom_tool", name)
        if not record:
            return {"ok": False, "tool": name, "error": "not found"}
        versions = list(record.get("versions") or [])
        if len(versions) > 1:
            current = versions[-1]
            previous = versions[-2]
            rolled_back_at = now_ms()
            record.update({
                "version": previous["version"],
                "registeredAt": previous.get("registeredAt", record.get("registeredAt")),
                "status": "active",
                "catalogRow": previous.get("catalogRow") or record.get("catalogRow"),
                "implementation": previous.get("implementation") or record.get("implementation"),
                "wrapperPath": previous.get("wrapperPath"),
                "wrapperDigest": previous.get("wrapperDigest"),
                "versions": versions[:-1],
                "rolledBackAt": rolled_back_at,
                "rolledBackBy": actor,
                "rollbackReason": reason,
                "history": [
                    *list(record.get("history") or []),
                    {
                        "event": "rollback",
                        "fromVersion": current.get("version"),
                        "toVersion": previous.get("version"),
                        "rolledBackAt": rolled_back_at,
                        "actor": actor,
                        "reason": reason,
                    },
                ],
            })
            await repo.put_entity("custom_tool", record, status="active", ts=rolled_back_at)
            return {
                "ok": True,
                "tool": name,
                "status": "active",
                "fromVersion": current.get("version"),
                "toVersion": previous.get("version"),
                "reason": reason,
            }
        record["status"] = "deprecated"
        record["deprecatedAt"] = now_ms()
        record["deprecatedBy"] = actor
        record["deprecationReason"] = reason
        record["history"] = [
            *list(record.get("history") or []),
            {
                "event": "deprecate",
                "deprecatedAt": record["deprecatedAt"],
                "actor": actor,
                "reason": reason,
            },
        ]
        await repo.put_entity("custom_tool", record, status="deprecated", ts=record["deprecatedAt"])
        return {"ok": True, "tool": name, "status": "deprecated", "reason": reason}


async def custom_tool_record(repo: Any, name: str) -> dict[str, Any] | None:
    row = await repo.get_entity("custom_tool", name)
    if not row or row.get("status") != "active":
        return None
    catalog = row.get("catalogRow")
    if not isinstance(catalog, dict) or catalog.get("tool") != name:
        return None
    return row


async def custom_tool_catalog_row(repo: Any, name: str) -> dict[str, Any] | None:
    row = await custom_tool_record(repo, name)
    catalog = row.get("catalogRow") if row else None
    return dict(catalog) if isinstance(catalog, dict) else None


def _workspace_root() -> Path:
    return get_settings().workspace_dir.resolve()


def _assert_wrapper_inside_workspace(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    resolved.relative_to(_workspace_root())
    return resolved


def _clip_output(text: str, limit: int) -> str:
    if len(text.encode("utf-8")) <= limit:
        return text
    encoded = text.encode("utf-8")[:limit]
    return encoded.decode("utf-8", errors="ignore") + "\n...[truncated]"


def _execute_template_tool(row: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    implementation = row.get("implementation") if isinstance(row.get("implementation"), dict) else {}
    template = str(implementation.get("template") or "")
    return {"rendered": template.format_map(_FormatArgs(args))}


def _execute_python_tool(row: dict[str, Any], args: dict[str, Any], *, dept_id: str) -> dict[str, Any]:
    implementation = row.get("implementation") if isinstance(row.get("implementation"), dict) else {}
    catalog = row.get("catalogRow") if isinstance(row.get("catalogRow"), dict) else {}
    wrapper_path = row.get("wrapperPath")
    if not isinstance(wrapper_path, str) or not wrapper_path:
        raise RuntimeError("custom Python tool is missing wrapperPath")
    path = _assert_wrapper_inside_workspace(Path(wrapper_path))
    if not path.exists() or not path.is_file():
        raise RuntimeError("custom Python tool wrapper is missing on disk")
    expected_digest = row.get("wrapperDigest")
    if expected_digest:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected_digest:
            raise RuntimeError("custom Python tool wrapper digest mismatch")
    timeout = max(1.0, min(float(implementation.get("timeoutSeconds") or 10), 120.0))
    output_limit = max(1000, min(int(catalog.get("outputLimitBytes") or 60_000), 1_000_000))
    cwd = get_settings().workspace_dir / dept_id
    cwd.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [sys.executable, str(path)],
        input=json.dumps(args, ensure_ascii=False),
        text=True,
        capture_output=True,
        cwd=cwd,
        timeout=timeout,
        check=False,
    )
    stdout = _clip_output(completed.stdout or "", output_limit)
    stderr = _clip_output(completed.stderr or "", output_limit)
    if completed.returncode != 0:
        raise RuntimeError(stderr or stdout or f"custom tool exited {completed.returncode}")
    try:
        parsed = json.loads(stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"custom tool returned non-JSON output: {stdout[:1000]}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("custom tool output must be a JSON object")
    if stderr:
        parsed.setdefault("stderr", stderr)
    return parsed


async def execute_custom_tool(repo: Any, run: dict[str, Any]) -> dict[str, Any] | None:
    row = await custom_tool_record(repo, str(run.get("tool") or ""))
    if not row:
        return None
    args = run.get("args") if isinstance(run.get("args"), dict) else {}
    implementation = row.get("implementation") if isinstance(row.get("implementation"), dict) else {}
    kind = str(implementation.get("kind") or "template").lower()
    if kind == "template":
        return _execute_template_tool(row, args)
    if kind == "python":
        return _execute_python_tool(row, args, dept_id=str(run.get("departmentId") or ""))
    raise RuntimeError(f"unsupported custom tool implementation kind: {kind}")


async def load_custom_tools(repo: Any, registry: ToolRegistry) -> int:
    rows = await repo.list_entities("custom_tool", status="active", limit=500)
    count = 0
    for row in rows:
        catalog = row.get("catalogRow")
        if isinstance(catalog, dict) and catalog.get("tool"):
            registry.register(ToolSpec(
                name=catalog["tool"],
                risk_class=catalog.get("riskClass", "safe_read"),
                description=catalog.get("description", ""),
                mutates_state=bool(catalog.get("mutatesState")),
                external_system=bool(catalog.get("externalSystem")),
                executor=catalog.get("executor", "host"),
                default_timeout_ms=int(catalog.get("defaultTimeoutMs") or 10_000),
                supports_checkpoint=bool(catalog.get("supportsCheckpoint")),
                rollback_capable=bool(catalog.get("rollbackCapable")),
                input_schema=dict(catalog.get("inputSchema") or {}),
                output_schema=dict(catalog.get("outputSchema") or {}),
                redaction_rules=list(catalog.get("redactionRules") or []),
            ))
            count += 1
    return count


async def sync_custom_tools_to_runtime(repo: Any, runtime: Any) -> dict[str, Any]:
    rows = await repo.list_entities("custom_tool", status="active", limit=500)
    synced = 0
    errors: list[dict[str, str]] = []
    for row in rows:
        tool_name = str(row.get("tool") or row.get("id") or "").strip()
        catalog = row.get("catalogRow") if isinstance(row.get("catalogRow"), dict) else None
        if not tool_name or not catalog:
            continue
        try:
            registration = await runtime.register_tool(tool_name, catalog)
            row["runtimeRegistration"] = registration
            row["history"] = [
                *list(row.get("history") or []),
                {
                    "event": "runtime_sync",
                    "syncedAt": now_ms(),
                    "backend": registration.get("backend"),
                    "ok": bool(registration.get("ok")),
                    "registered": bool(registration.get("registered")),
                },
            ]
            await repo.put_entity("custom_tool", row, status="active", ts=now_ms())
            synced += 1
        except Exception as exc:
            errors.append({"tool": tool_name, "error": f"{type(exc).__name__}: {exc}"})
    return {"ok": not errors, "synced": synced, "errors": errors}
