from contextlib import asynccontextmanager
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
