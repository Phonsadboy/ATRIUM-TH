import unittest
from unittest import mock

from fastapi import HTTPException

from app.schema import TaskControlInput


def _task(**overrides):
    base = {
        "id": "task_1",
        "title": "ตรวจงานซ้ำ",
        "detail": "รายละเอียด",
        "status": "in_progress",
        "priority": "normal",
        "departmentId": "research",
        "origin": {"kind": "executive"},
        "progress": 0.4,
        "createdAt": 1,
        "updatedAt": 1,
        "handoffs": [],
        "log": ["เริ่มงาน"],
        "result": {},
    }
    base.update(overrides)
    return base


class FakeRepo:
    def __init__(self) -> None:
        self.departments = {
            "research": {
                "id": "research",
                "name": "วิจัย",
                "agentName": "รีเสิร์ช",
                "state": "working",
                "currentTaskId": "task_1",
            }
        }
        self.tasks = {}
        self.activities = []
        self.approvals = []
        self.entities = {}
        self.messages = {}
        self.cancelled_jobs = []
        self.enqueued = []

    async def save_task(self, task):
        self.tasks[task["id"]] = dict(task)

    async def save_department(self, dept):
        self.departments[dept["id"]] = dict(dept)

    async def add_activity(self, activity):
        self.activities.append(dict(activity))

    async def list_approvals(self, *, status=None, limit=None):
        items = [dict(item) for item in self.approvals if status is None or item.get("status") == status]
        return items[:limit] if limit is not None else items

    async def save_approval(self, approval):
        for index, item in enumerate(self.approvals):
            if item["id"] == approval["id"]:
                self.approvals[index] = dict(approval)
                return
        self.approvals.append(dict(approval))

    async def get_message(self, msg_id):
        return self.messages.get(msg_id)

    async def add_message(self, msg):
        self.messages[msg["id"]] = dict(msg)

    async def update_message(self, msg):
        self.messages[msg["id"]] = dict(msg)

    async def cancel_task_jobs(self, task_id, reason):
        self.cancelled_jobs.append((task_id, reason))
        return 2

    async def get_entity(self, type_, entity_id):
        return self.entities.get((type_, entity_id))

    async def enqueue(self, job_id, kind, payload, run_after, priority=5):
        self.enqueued.append({
            "jobId": job_id,
            "kind": kind,
            "payload": dict(payload),
            "runAfter": run_after,
            "priority": priority,
        })


class TaskControlModalBackendTest(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_releases_department_cancels_jobs_and_rejects_pending_close_approval(self) -> None:
        from app import main

        repo = FakeRepo()
        task = _task(
            status="review",
            pendingCloseApprovalId="apr_1",
            waitingOn={"dept": "exec", "approvalId": "apr_1", "reason": "task_close_approval"},
        )
        repo.approvals.append({
            "id": "apr_1",
            "ts": 1,
            "kind": "task_close",
            "title": "อนุมัติปิดงาน: ตรวจงานซ้ำ",
            "detail": "รอผู้บริหาร AI ตรวจ",
            "departmentId": "research",
            "status": "pending",
            "action": {"action": "close_task", "taskId": "task_1", "departmentId": "research"},
        })

        result = await main.apply_user_task_control(
            repo,
            task,
            repo.departments["research"],
            TaskControlInput(action="cancel", reason="งานซ้ำ", requestedBy="user"),
            1_000,
        )

        saved = repo.tasks["task_1"]
        self.assertTrue(result["executed"])
        self.assertEqual(saved["status"], "cancelled")
        self.assertIsNone(saved["waitingOn"])
        self.assertNotIn("pendingCloseApprovalId", saved)
        self.assertIsNone(repo.departments["research"]["currentTaskId"])
        self.assertEqual(repo.departments["research"]["state"], "idle")
        self.assertEqual(repo.cancelled_jobs, [("task_1", "งานซ้ำ")])
        self.assertEqual(repo.approvals[0]["status"], "rejected")
        self.assertEqual(repo.approvals[0]["resolvedBy"], "user")
        self.assertEqual(repo.messages["msg_apr_1"]["approvalStatus"], "rejected")

    async def test_pause_and_resume_use_user_pause_marker_without_terminal_status(self) -> None:
        from app import main
        from app.engine import PAUSED_BY_USER_REASON

        repo = FakeRepo()
        task = _task(nextReviewAt=2_000, reviewScheduleToken="rev_1")

        paused = await main.apply_user_task_control(
            repo,
            task,
            repo.departments["research"],
            TaskControlInput(action="pause", reason="รอข้อมูล", requestedBy="user"),
            1_000,
        )

        self.assertTrue(paused["executed"])
        self.assertEqual(task["status"], "blocked")
        self.assertEqual(task["statusReason"], PAUSED_BY_USER_REASON)
        self.assertIsNone(task["nextReviewAt"])
        self.assertIsNone(task["reviewScheduleToken"])
        self.assertIsNone(repo.departments["research"]["currentTaskId"])

        resumed = await main.apply_user_task_control(
            repo,
            task,
            repo.departments["research"],
            TaskControlInput(action="resume", requestedBy="user"),
            2_000,
        )

        self.assertTrue(resumed["executed"])
        self.assertEqual(task["status"], "assigned")
        self.assertIsNone(task["statusReason"])

    async def test_close_marks_done_by_user_and_releases_department(self) -> None:
        from app import main

        repo = FakeRepo()
        task = _task(status="waiting", waitingOn={"dept": "exec", "reason": "task_close_approval"})

        result = await main.apply_user_task_control(
            repo,
            task,
            repo.departments["research"],
            TaskControlInput(action="close", reason="ไม่ต้องตรวจซ้ำ", requestedBy="user"),
            1_000,
        )

        saved = repo.tasks["task_1"]
        self.assertTrue(result["executed"])
        self.assertEqual(saved["status"], "done")
        self.assertEqual(saved["progress"], 1)
        self.assertEqual(saved["result"]["reviewStatus"], "closed_by_user")
        self.assertEqual(saved["result"]["closedBy"], "user")
        self.assertEqual(saved["result"]["completedAt"], 1_000)
        self.assertIsNone(repo.departments["research"]["currentTaskId"])

    async def test_submit_partial_uses_existing_output_and_marks_request_as_user(self) -> None:
        from app import main

        repo = FakeRepo()
        repo.entities[("artifact", "art_1")] = {"id": "art_1", "name": "partial"}
        task = _task(draftDeliverableMarkdown="สรุปเท่าที่มี")
        captured = {}

        async def fake_request_close(repo_arg, dept_arg, task_arg, now_arg, **kwargs):
            captured.update(kwargs)
            task_arg["status"] = "review"
            task_arg["waitingOn"] = {"dept": "exec", "approvalId": "apr_partial", "reason": "task_close_approval"}
            task_arg["updatedAt"] = now_arg
            await repo_arg.save_task(task_arg)
            return {
                "id": "apr_partial",
                "status": "pending",
                "action": {
                    "action": "close_task",
                    "taskId": task_arg["id"],
                    "departmentId": dept_arg["id"],
                    "artifactId": "art_1",
                    "requestedBy": dept_arg["id"],
                },
            }

        with mock.patch.object(main, "request_task_close_approval", new=fake_request_close):
            result = await main.apply_user_task_control(
                repo,
                task,
                repo.departments["research"],
                TaskControlInput(action="submit_partial", requestedBy="user"),
                1_000,
            )

        self.assertTrue(result["executed"])
        self.assertEqual(captured["source"], "user_submit_partial")
        self.assertEqual(captured["content"], "สรุปเท่าที่มี")
        self.assertEqual(result["approval"]["action"]["requestedBy"], "user")
        self.assertEqual(result["approval"]["action"]["source"], "user_submit_partial")
        self.assertEqual(result["artifact"]["id"], "art_1")

    async def test_submit_partial_requires_some_output(self) -> None:
        from app import main

        repo = FakeRepo()
        task = _task(log=[])

        with self.assertRaises(HTTPException) as raised:
            await main.apply_user_task_control(
                repo,
                task,
                repo.departments["research"],
                TaskControlInput(action="submit_partial", requestedBy="user"),
                1_000,
            )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(raised.exception.detail, "ยังไม่มีผลลัพธ์พอให้ส่ง")


if __name__ == "__main__":
    unittest.main()
