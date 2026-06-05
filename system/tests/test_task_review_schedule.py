from contextlib import asynccontextmanager
from pathlib import Path
import tempfile
import unittest
from unittest import mock


class TaskReviewScheduleHelperTest(unittest.TestCase):
    def test_new_task_review_interval_defaults_by_priority(self) -> None:
        from app.task_review import review_interval_for_new_task

        self.assertEqual(review_interval_for_new_task(None, priority="urgent"), 2 * 60_000)
        self.assertEqual(review_interval_for_new_task(None, priority="high"), 3 * 60_000)
        self.assertEqual(review_interval_for_new_task(None, priority="normal"), 5 * 60_000)
        self.assertEqual(review_interval_for_new_task(None, priority="low"), 10 * 60_000)

    def test_new_task_review_interval_allows_explicit_disable(self) -> None:
        from app.task_review import review_interval_for_new_task

        self.assertIsNone(review_interval_for_new_task(0, priority="urgent"))
        self.assertIsNone(review_interval_for_new_task(-1, priority="urgent"))


class FakeRepo:
    def __init__(self) -> None:
        self.departments = {
            "exec": {
                "id": "exec",
                "name": "ผู้บริหาร",
                "agentName": "ออตโต้",
                "state": "working",
                "currentTaskId": "task_exec_existing",
            },
            "research": {
                "id": "research",
                "name": "วิจัย",
                "agentName": "รีเสิร์ช",
                "state": "working",
                "currentTaskId": "task_existing",
            }
        }
        self.tasks = []
        self.activities = []
        self.enqueued = []

    async def get_department(self, dept_id):
        return self.departments.get(dept_id)

    async def get_entity(self, type_, entity_id):
        return None

    async def get_task(self, task_id):
        return None

    async def save_task(self, task):
        self.tasks.append(dict(task))

    async def save_department(self, dept):
        self.departments[dept["id"]] = dict(dept)

    async def add_activity(self, activity):
        self.activities.append(dict(activity))

    async def enqueue(self, job_id, kind, payload, run_at, *, priority=0):
        self.enqueued.append({
            "jobId": job_id,
            "kind": kind,
            "payload": dict(payload),
            "runAt": run_at,
            "priority": priority,
        })


class ChatCreateTaskReviewScheduleTest(unittest.IsolatedAsyncioTestCase):
    def test_create_task_schema_includes_executive_target(self) -> None:
        from app import chat_tools

        definitions = chat_tools.chat_tool_definitions(
            [
                {"id": "exec", "name": "ผู้บริหาร"},
                {"id": "research", "name": "วิจัย"},
            ],
            {"id": "exec", "name": "ผู้บริหาร"},
        )
        create_task = next(item for item in definitions if item["name"] == "create_task")

        self.assertEqual(
            create_task["input_schema"]["properties"]["departmentId"]["enum"],
            ["exec", "research"],
        )

    def test_create_task_schema_excludes_executive_target_for_department_agent(self) -> None:
        from app import chat_tools

        definitions = chat_tools.chat_tool_definitions(
            [
                {"id": "exec", "name": "ผู้บริหาร"},
                {"id": "research", "name": "วิจัย"},
            ],
            {"id": "research", "name": "วิจัย"},
        )
        create_task = next(item for item in definitions if item["name"] == "create_task")

        self.assertEqual(
            create_task["input_schema"]["properties"]["departmentId"]["enum"],
            ["research"],
        )

    async def test_create_task_defaults_review_schedule_and_enqueues_reminder(self) -> None:
        from app import chat_tools
        from app.task_review import TASK_REVIEW_REMINDER_KIND

        repo = FakeRepo()
        active = {"id": "exec", "name": "ผู้บริหาร", "agentName": "ออตโต้"}

        with (
            mock.patch.object(chat_tools, "now_ms", return_value=1_000_000),
            mock.patch.object(chat_tools, "emit_work_status_notice", new=mock.AsyncMock()),
        ):
            result = await chat_tools._create_task_tool(
                repo,
                {"title": "ตรวจตลาด", "departmentId": "research", "priority": "high"},
                active,
            )

        task = result["task"]
        self.assertTrue(result["ok"])
        self.assertEqual(task["reviewIntervalMs"], 3 * 60_000)
        self.assertEqual(task["nextReviewAt"], 1_000_000 + 3 * 60_000)
        self.assertTrue(task["reviewScheduleToken"].startswith("rev_"))
        self.assertIn("3 นาที", "\n".join(task["log"]))
        self.assertEqual(len(repo.enqueued), 1)
        self.assertEqual(repo.enqueued[0]["kind"], TASK_REVIEW_REMINDER_KIND)
        self.assertEqual(repo.enqueued[0]["runAt"], task["nextReviewAt"])
        self.assertEqual(repo.enqueued[0]["payload"]["reviewIntervalMs"], 3 * 60_000)

    async def test_create_task_allows_executive_as_target_department(self) -> None:
        from app import chat_tools

        repo = FakeRepo()
        active = {"id": "exec", "name": "ผู้บริหาร", "agentName": "ออตโต้"}

        with (
            mock.patch.object(chat_tools, "now_ms", return_value=1_000_000),
            mock.patch.object(chat_tools, "emit_work_status_notice", new=mock.AsyncMock()),
        ):
            result = await chat_tools._create_task_tool(
                repo,
                {"title": "วาง policy โมเดล", "departmentId": "exec", "priority": "normal"},
                active,
            )

        task = result["task"]
        self.assertTrue(result["ok"])
        self.assertEqual(task["departmentId"], "exec")
        self.assertEqual(task["origin"], {"kind": "executive"})
        self.assertEqual(task["watchers"], ["executive"])
        self.assertEqual(task["reviewIntervalMs"], 5 * 60_000)
        self.assertIn("ผู้บริหาร", repo.activities[-1]["text"])
        self.assertNotIn("ฝ่ายผู้บริหาร", repo.activities[-1]["text"])


class EngineExecutiveSelfCloseTest(unittest.IsolatedAsyncioTestCase):
    async def test_executive_self_task_closes_directly_without_pending_approval(self) -> None:
        from app import engine

        class EngineRepo:
            def __init__(self) -> None:
                self.departments = {
                    "exec": {
                        "id": "exec",
                        "name": "ผู้บริหาร",
                        "agentName": "ออตโต้",
                        "state": "review",
                        "currentTaskId": "task_self",
                    }
                }
                self.tasks = {
                    "task_self": {
                        "id": "task_self",
                        "title": "วาง policy โมเดล",
                        "detail": "จัดทำแนวทาง provider/model",
                        "departmentId": "exec",
                        "status": "review",
                        "progress": 0.95,
                        "updatedAt": 1,
                        "log": ["เริ่มงาน", "พร้อมปิด"],
                        "deliverables": [],
                        "result": {},
                    }
                }
                self.approvals = []
                self.entities = {}
                self.activities = []

            async def list_approvals(self):
                return [dict(item) for item in self.approvals]

            async def get_entity(self, type_, entity_id):
                return self.entities.get((type_, entity_id))

            async def put_entity(self, type_, entity, **_kwargs):
                self.entities[(type_, entity["id"])] = dict(entity)

            async def get_task(self, task_id):
                return self.tasks.get(task_id)

            async def save_task(self, task):
                self.tasks[task["id"]] = dict(task)

            async def save_department(self, dept):
                self.departments[dept["id"]] = dict(dept)

            async def add_approval(self, approval):
                self.approvals.append(dict(approval))

            async def save_approval(self, approval):
                self.approvals = [dict(approval) if item["id"] == approval["id"] else item for item in self.approvals]

            async def add_activity(self, activity):
                self.activities.append(dict(activity))

        repo = EngineRepo()

        with tempfile.TemporaryDirectory() as tmp_dir:
            def artifact_path(dept_id, artifact_id, version):
                path = Path(tmp_dir) / dept_id / artifact_id / f"v{version}.md"
                path.parent.mkdir(parents=True, exist_ok=True)
                return str(path)

            with (
                mock.patch.object(engine, "_artifact_content_path", side_effect=artifact_path),
                mock.patch.object(engine, "emit_work_status_notice", new=mock.AsyncMock()),
                mock.patch.object(engine, "_enqueue_task_done_reflection", new=mock.AsyncMock()),
                mock.patch("app.eval.scoring.record_task_outcome", new=mock.AsyncMock()),
            ):
                approval = await engine.request_task_close_approval(
                    repo,
                    repo.departments["exec"],
                    repo.tasks["task_self"],
                    1_000_000,
                    content="สรุป policy โมเดลพร้อมใช้งาน",
                    decision={"rationale": "งานผู้บริหารตรวจเองได้"},
                    source="engine_review",
                )

        task = repo.tasks["task_self"]
        self.assertEqual(approval["status"], "approved")
        self.assertEqual(approval["resolvedBy"], "exec")
        self.assertEqual(approval["autoApprovedBy"], "executive_self_close")
        self.assertEqual(approval["action"]["executedAt"], 1_000_000)
        self.assertEqual(len(repo.approvals), 1)
        self.assertEqual(repo.approvals[0]["status"], "approved")
        self.assertEqual(task["status"], "done")
        self.assertEqual(task["result"]["reviewStatus"], "closed_by_executive_self")
        self.assertNotIn("waitingOn", task)
        self.assertNotIn("pendingCloseApprovalId", task)
        self.assertEqual(repo.departments["exec"]["state"], "idle")
        self.assertIsNone(repo.departments["exec"]["currentTaskId"])
        artifact_id = approval["action"]["artifactId"]
        self.assertEqual(repo.entities[("artifact", artifact_id)]["status"], "approved")


class ApiAssignTaskCollisionTest(unittest.IsolatedAsyncioTestCase):
    async def test_assign_task_rejects_empty_title(self) -> None:
        from app import main
        from app.schema import AssignTaskInput
        from fastapi import HTTPException

        repo = FakeRepo()

        @asynccontextmanager
        async def fake_session_scope():
            yield object()

        with (
            mock.patch.object(main, "session_scope", fake_session_scope),
            mock.patch.object(main, "Repo", lambda _session: repo),
        ):
            with self.assertRaises(HTTPException) as raised:
                await main.assign_task(
                    AssignTaskInput(
                        title="   ",
                        department_id="research",
                    )
                )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(raised.exception.detail, "title cannot be empty")
        self.assertEqual(repo.tasks, [])

    async def test_assign_task_rejects_client_id_collision(self) -> None:
        from app import main
        from app.schema import AssignTaskInput
        from fastapi import HTTPException

        class CollisionRepo(FakeRepo):
            async def get_task(self, task_id):
                if task_id == "task_existing":
                    return {"id": task_id, "title": "เดิม", "status": "assigned"}
                return None

        repo = CollisionRepo()

        @asynccontextmanager
        async def fake_session_scope():
            yield object()

        with (
            mock.patch.object(main, "session_scope", fake_session_scope),
            mock.patch.object(main, "Repo", lambda _session: repo),
        ):
            with self.assertRaises(HTTPException) as raised:
                await main.assign_task(
                    AssignTaskInput(
                        id="task_existing",
                        title="งานใหม่",
                        department_id="research",
                    )
                )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.detail, "task id already exists")
        self.assertEqual(repo.tasks, [])

    async def test_assign_task_rejects_closed_parent_task(self) -> None:
        from app import main
        from app.schema import AssignTaskInput
        from fastapi import HTTPException

        class ParentRepo(FakeRepo):
            async def get_task(self, task_id):
                if task_id == "task_parent":
                    return {"id": task_id, "title": "แม่", "status": "done", "projectId": None}
                return None

        repo = ParentRepo()

        @asynccontextmanager
        async def fake_session_scope():
            yield object()

        with (
            mock.patch.object(main, "session_scope", fake_session_scope),
            mock.patch.object(main, "Repo", lambda _session: repo),
        ):
            with self.assertRaises(HTTPException) as raised:
                await main.assign_task(
                    AssignTaskInput(
                        title="งานลูก",
                        department_id="research",
                        parent_task_id="task_parent",
                    )
                )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(raised.exception.detail, "parent task is closed")
        self.assertEqual(repo.tasks, [])

    async def test_assign_task_rejects_cross_project_parent_task(self) -> None:
        from app import main
        from app.schema import AssignTaskInput
        from fastapi import HTTPException

        class ParentRepo(FakeRepo):
            async def get_task(self, task_id):
                if task_id == "task_parent":
                    return {"id": task_id, "title": "แม่", "status": "assigned", "projectId": "proj_a"}
                return None

            async def get_entity(self, type_, entity_id):
                if type_ == "project" and entity_id == "proj_b":
                    return {"id": entity_id, "departments": ["research"]}
                return None

        repo = ParentRepo()

        @asynccontextmanager
        async def fake_session_scope():
            yield object()

        with (
            mock.patch.object(main, "session_scope", fake_session_scope),
            mock.patch.object(main, "Repo", lambda _session: repo),
        ):
            with self.assertRaises(HTTPException) as raised:
                await main.assign_task(
                    AssignTaskInput(
                        title="งานลูก",
                        department_id="research",
                        project_id="proj_b",
                        parent_task_id="task_parent",
                    )
                )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(raised.exception.detail, "parent task project mismatch")
        self.assertEqual(repo.tasks, [])


class TaskReviewReminderRescanTest(unittest.IsolatedAsyncioTestCase):
    async def test_rescan_reenqueues_scheduled_task_without_active_reminder(self) -> None:
        from app import engine
        from app.task_review import TASK_REVIEW_REMINDER_KIND

        class RescanRepo(FakeRepo):
            def __init__(self) -> None:
                super().__init__()
                self.tasks = [{
                    "id": "task_due",
                    "title": "ตรวจงาน",
                    "status": "in_progress",
                    "departmentId": "research",
                    "reviewIntervalMs": 5 * 60_000,
                    "reviewScheduleToken": "rev_due",
                    "nextReviewAt": 1_500_000,
                }]

            async def list_active_tasks(self, limit=None):
                return self.tasks

            async def active_jobs(self, limit=200):
                return []

        repo = RescanRepo()
        count = await engine._rescan_task_review_reminders(repo, 1_000_000)

        self.assertEqual(count, 1)
        self.assertEqual(len(repo.enqueued), 1)
        self.assertEqual(repo.enqueued[0]["kind"], TASK_REVIEW_REMINDER_KIND)
        self.assertEqual(repo.enqueued[0]["payload"]["taskId"], "task_due")
        self.assertEqual(repo.enqueued[0]["payload"]["reviewScheduleToken"], "rev_due")
        self.assertEqual(repo.enqueued[0]["runAt"], 1_500_000)

    async def test_rescan_does_not_duplicate_active_reminder_for_same_token(self) -> None:
        from app import engine
        from app.task_review import TASK_REVIEW_REMINDER_KIND

        class RescanRepo(FakeRepo):
            def __init__(self) -> None:
                super().__init__()
                self.tasks = [{
                    "id": "task_due",
                    "title": "ตรวจงาน",
                    "status": "in_progress",
                    "departmentId": "research",
                    "reviewIntervalMs": 5 * 60_000,
                    "reviewScheduleToken": "rev_due",
                    "nextReviewAt": 1_500_000,
                }]

            async def list_active_tasks(self, limit=None):
                return self.tasks

            async def active_jobs(self, limit=200):
                return [{
                    "id": "job_existing",
                    "kind": TASK_REVIEW_REMINDER_KIND,
                    "status": "queued",
                    "payload": {"taskId": "task_due", "reviewScheduleToken": "rev_due"},
                }]

        repo = RescanRepo()
        count = await engine._rescan_task_review_reminders(repo, 1_000_000)

        self.assertEqual(count, 0)
        self.assertEqual(repo.enqueued, [])


if __name__ == "__main__":
    unittest.main()
