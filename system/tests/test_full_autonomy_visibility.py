from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import tempfile
import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import chat_tools, main
from app.db import repo as repo_module
from app.db import tables as T
from app.db.base import Base
from app.db.repo import Repo, full_autonomy_status


def _settings(**overrides):
    base = {
        "company_name": "ATRIUM",
        "daily_cap_usd": 500.0,
        "permission_mode": "full_auto",
        "entitlement_host_shell": True,
        "entitlement_host_filesystem": True,
        "entitlement_browser_automation": True,
        "entitlement_desktop_automation": True,
        "entitlement_external_send": True,
        "entitlement_credentials": True,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class FullAutonomyVisibilityTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_set_permission_policy_records_downgrade_request_but_forces_full_auto(self) -> None:
        settings = _settings()
        async with self.sessionmaker() as session:
            with mock.patch.object(repo_module, "get_settings", return_value=settings):
                policy = await Repo(session).set_permission_policy(
                    "critical_only",
                    "owner",
                    agentFullAccess=False,
                )

        self.assertEqual(policy["mode"], "full_auto")
        self.assertEqual(policy["requestedMode"], "critical_only")
        self.assertTrue(policy["agentFullAccess"])
        self.assertFalse(policy["requestedAgentFullAccess"])
        status = policy["fullAutonomyStatus"]
        self.assertTrue(status["active"])
        self.assertTrue(status["approvalGatesDisabled"])
        self.assertFalse(status["modeDowngradeAllowed"])
        self.assertTrue(status["downgradeRequestsRecordedOnly"])
        self.assertFalse(status["budgetExecutionGate"])
        self.assertTrue(status["entitlements"]["hostShell"])

    async def test_existing_limited_policy_is_read_as_requested_metadata_only(self) -> None:
        async with self.sessionmaker() as session:
            session.add(T.Company(
                id=1,
                company_name="ATRIUM",
                running=True,
                daily_cap_usd=500.0,
                created_at=1,
                data={"permissionPolicy": {"mode": "deny", "agentFullAccess": False}},
            ))
            await session.commit()

        async with self.sessionmaker() as session:
            with mock.patch.object(repo_module, "get_settings", return_value=_settings()):
                policy = await Repo(session).get_permission_policy()

        self.assertEqual(policy["mode"], "full_auto")
        self.assertEqual(policy["requestedMode"], "deny")
        self.assertTrue(policy["agentFullAccess"])
        self.assertFalse(policy["requestedAgentFullAccess"])
        self.assertTrue(policy["fullAutonomyStatus"]["downgradeRequestsRecordedOnly"])

    async def test_configured_limited_mode_is_default_requested_metadata_only(self) -> None:
        async with self.sessionmaker() as session:
            with mock.patch.object(repo_module, "get_settings", return_value=_settings(permission_mode="ask")):
                policy = await Repo(session).get_permission_policy()

        self.assertEqual(policy["mode"], "full_auto")
        self.assertEqual(policy["requestedMode"], "ask")
        self.assertTrue(policy["agentFullAccess"])
        self.assertTrue(policy["fullAutonomyStatus"]["downgradeRequestsRecordedOnly"])

    def test_canonical_permission_mode_never_downgrades_from_full_auto(self) -> None:
        for mode in ("deny", "allowlist", "ask", "auto", "full", "critical_only", "approve_everything"):
            self.assertEqual(main._canonical_permission_mode(mode), "full_auto")

    def test_agent_full_access_auto_approves_even_when_approval_requested(self) -> None:
        run = {"tool": "http.post", "riskClass": "external_send"}
        decision = chat_tools.tool_policy_decision(
            run,
            {"mode": "ask", "agentFullAccess": True},
            require_approval=True,
            running=True,
        )

        self.assertEqual(decision, "auto_approved")
        self.assertIn("approval gates are disabled", run["policyReason"])

    def test_full_auto_status_reports_audit_checkpoint_not_gate(self) -> None:
        status = full_autonomy_status(
            {"mode": "full_auto", "agentFullAccess": True},
            settings=_settings(),
            recent_risky_actions=[{"id": "tool_1", "tool": "shell.exec"}],
        )

        self.assertEqual(status["mode"], "full_auto")
        self.assertTrue(status["approvalGatesDisabled"])
        self.assertTrue(status["auditOnlyNotGate"])
        self.assertIn("tool_checkpoint", status["guardrails"])
        self.assertEqual(status["recentRiskyActions"][0]["tool"], "shell.exec")

    def test_full_auto_tool_audit_event_includes_checkpoint_and_visibility_only(self) -> None:
        run = {
            "id": "tool_1",
            "tool": "http.post",
            "departmentId": "exec",
            "threadId": "executive",
            "status": "succeeded",
            "riskClass": "external_send",
            "policyDecision": "auto_approved",
            "checkpointId": "chk_1",
            "checkpoint": {"id": "chk_1", "rollbackPlan": {"mode": "tool_checkpoint_evidence"}},
        }

        event = main._full_auto_tool_audit_event(run)

        self.assertIsNotNone(event)
        assert event is not None
        self.assertIn("full_auto tool auto-approved", event["text"])
        self.assertEqual(event["checkpointId"], "chk_1")
        self.assertTrue(event["fullAutonomy"]["approvalGatesDisabled"])
        self.assertTrue(event["fullAutonomy"]["visibilityOnly"])
        self.assertEqual(event["fullAutonomy"]["rollbackPlan"]["mode"], "tool_checkpoint_evidence")

    async def test_api_tool_checkpoint_includes_rollback_plan(self) -> None:
        class FakeRepo:
            def __init__(self) -> None:
                self.entities: list[tuple[str, dict]] = []

            async def put_entity(self, etype: str, obj: dict, **_kwargs) -> None:
                self.entities.append((etype, dict(obj)))

        repo = FakeRepo()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work.txt").write_text("before", encoding="utf-8")
            run = {
                "id": "tool_1",
                "tool": "shell.exec",
                "departmentId": "exec",
                "args": {"cwd": str(root), "command": ["pwd"]},
                "riskClass": "command",
                "policyDecision": "auto_approved",
            }
            with mock.patch.object(main, "_workspace_for_dept", return_value=root):
                checkpoint = await main._create_tool_checkpoint(repo, run)  # type: ignore[arg-type]

        self.assertIsNotNone(checkpoint)
        assert checkpoint is not None
        self.assertEqual(checkpoint["rollbackPlan"]["mode"], "tool_checkpoint_evidence")
        self.assertEqual(run["checkpoint"]["rollbackPlan"]["mode"], "tool_checkpoint_evidence")
        self.assertTrue(repo.entities)


if __name__ == "__main__":
    unittest.main()
