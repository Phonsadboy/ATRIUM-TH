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


if __name__ == "__main__":
    unittest.main()
