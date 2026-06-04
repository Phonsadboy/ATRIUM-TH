import asyncio
from types import SimpleNamespace
import unittest
from unittest import mock


class RuntimeJobStabilityTest(unittest.IsolatedAsyncioTestCase):
    async def test_general_engine_queue_excludes_dedicated_worker_jobs(self) -> None:
        from app import engine

        seen: dict[str, object] = {}

        async def fake_claim_due_jobs(repo, now, *, limit, kind=None, exclude_kinds=None):
            seen["kind"] = kind
            seen["exclude_kinds"] = exclude_kinds
            return []

        settings = SimpleNamespace(engine_job_timeout_s=1.0)
        with mock.patch.object(engine, "_claim_due_jobs", side_effect=fake_claim_due_jobs):
            processed = await engine._process_due_jobs(object(), 123, settings)

        self.assertEqual(processed, 0)
        self.assertIsNone(seen["kind"])
        self.assertEqual(seen["exclude_kinds"], {"image_generation", "trigger_run"})

    async def test_trigger_scheduler_keeps_trigger_jobs_in_its_own_queue(self) -> None:
        from app import engine

        seen: dict[str, object] = {}

        async def fake_claim_due_jobs(repo, now, *, limit, kind=None, exclude_kinds=None):
            seen["kind"] = kind
            seen["exclude_kinds"] = exclude_kinds
            return []

        settings = SimpleNamespace(engine_job_timeout_s=1.0)
        with mock.patch.object(engine, "_claim_due_jobs", side_effect=fake_claim_due_jobs):
            processed = await engine._process_due_jobs(object(), 123, settings, kind="trigger_run")

        self.assertEqual(processed, 0)
        self.assertEqual(seen["kind"], "trigger_run")
        self.assertIsNone(seen["exclude_kinds"])

    async def test_claim_due_jobs_releases_connection_after_marking_running(self) -> None:
        from app import engine

        job = SimpleNamespace(id="job_1")
        repo = SimpleNamespace(
            s=object(),
            due_jobs=mock.AsyncMock(return_value=[job]),
            mark_job=mock.AsyncMock(),
        )

        with mock.patch.object(engine, "commit_and_release", new=mock.AsyncMock()) as release:
            claimed = await engine._claim_due_jobs(repo, 123, limit=3)

        self.assertEqual(claimed, [job])
        repo.mark_job.assert_awaited_once_with("job_1", "running")
        release.assert_awaited_once_with(repo.s)

    async def test_claim_due_jobs_uses_atomic_repo_claim_when_available(self) -> None:
        from app import engine

        job = SimpleNamespace(id="job_1")
        repo = SimpleNamespace(
            s=object(),
            claim_due_jobs=mock.AsyncMock(return_value=[job]),
            due_jobs=mock.AsyncMock(),
            mark_job=mock.AsyncMock(),
        )

        with mock.patch.object(engine, "commit_and_release", new=mock.AsyncMock()) as release:
            claimed = await engine._claim_due_jobs(repo, 123, limit=3, kind="chat_reply")

        self.assertEqual(claimed, [job])
        repo.claim_due_jobs.assert_awaited_once_with(123, limit=3, kind="chat_reply", exclude_kinds=None)
        repo.due_jobs.assert_not_awaited()
        repo.mark_job.assert_not_awaited()
        release.assert_awaited_once_with(repo.s)

    def test_parallel_chat_partition_serializes_one_department(self) -> None:
        from app import engine

        jobs = [
            SimpleNamespace(id="job_a1", payload={"departmentId": "dept_a"}),
            SimpleNamespace(id="job_a2", payload={"departmentId": "dept_a"}),
            SimpleNamespace(id="job_b1", payload={"departmentId": "dept_b"}),
        ]

        runnable, deferred = engine._partition_parallel_chat_jobs(jobs, limit=3)

        self.assertEqual([job.id for job in runnable], ["job_a1", "job_b1"])
        self.assertEqual([job.id for job in deferred], ["job_a2"])

        runnable, deferred = engine._partition_parallel_chat_jobs(jobs, limit=3, busy_department_ids={"dept_a"})

        self.assertEqual([job.id for job in runnable], ["job_b1"])
        self.assertEqual([job.id for job in deferred], ["job_a1", "job_a2"])

    async def test_chat_reply_starter_runs_distinct_departments_in_parallel(self) -> None:
        from app import engine

        jobs = [
            SimpleNamespace(id="job_a1", kind="chat_reply", payload={"departmentId": "dept_a"}),
            SimpleNamespace(id="job_b1", kind="chat_reply", payload={"departmentId": "dept_b"}),
        ]
        running = 0
        max_running = 0
        both_started = asyncio.Event()
        release_jobs = asyncio.Event()
        claim_calls = 0

        async def fake_claim_due_job_records(now, *, kind, limit, exclude_kinds=None):
            nonlocal claim_calls
            claim_calls += 1
            return jobs if claim_calls == 1 else []

        async def fake_process_claimed_job_record(job, settings=None):
            nonlocal running, max_running
            running += 1
            max_running = max(max_running, running)
            if running == 2:
                both_started.set()
            try:
                await release_jobs.wait()
            finally:
                running -= 1
            return 1

        settings = SimpleNamespace(engine_job_timeout_s=1.0, chat_reply_worker_concurrency=2)
        in_flight: dict[asyncio.Task[int], SimpleNamespace] = {}
        with (
            mock.patch.object(engine, "_running_chat_reply_department_ids", new=mock.AsyncMock(return_value=set())),
            mock.patch.object(engine, "_claim_due_job_records", side_effect=fake_claim_due_job_records),
            mock.patch.object(engine, "_process_claimed_job_record", side_effect=fake_process_claimed_job_record),
        ):
            started = await engine._start_available_chat_reply_jobs(in_flight, settings)
            await asyncio.wait_for(both_started.wait(), timeout=1)
            release_jobs.set()
            results = await asyncio.gather(*in_flight.keys())

        self.assertEqual(started, 2)
        self.assertEqual(results, [1, 1])
        self.assertEqual(max_running, 2)

    async def test_department_advancement_runs_distinct_departments_in_parallel(self) -> None:
        from app import engine

        departments = [{"id": "dept_a"}, {"id": "dept_b"}, {"id": "exec"}]
        running = 0
        max_running = 0
        both_started = asyncio.Event()

        async def fake_advance_department_in_session(dept_id, *, departments, now):
            nonlocal running, max_running
            running += 1
            max_running = max(max_running, running)
            if running == 2:
                both_started.set()
            try:
                await asyncio.wait_for(both_started.wait(), timeout=1)
                await asyncio.sleep(0)
            finally:
                running -= 1
            return True

        settings = SimpleNamespace(department_worker_concurrency=2)
        with mock.patch.object(
            engine,
            "_advance_department_in_session",
            side_effect=fake_advance_department_in_session,
        ):
            changed = await engine._advance_departments_parallel(departments, 123, settings)

        self.assertEqual(changed, 2)
        self.assertEqual(max_running, 2)

    async def test_handoff_task_blocks_source_and_wakes_idle_target(self) -> None:
        from app import engine

        class FakeRepo:
            def __init__(self) -> None:
                self.departments = {
                    "target": {
                        "id": "target",
                        "name": "Target",
                        "agentName": "Target Agent",
                        "state": "idle",
                        "currentTaskId": None,
                    }
                }
                self.saved_tasks: list[dict] = []
                self.saved_departments: list[dict] = []
                self.activities: list[dict] = []
                self.messages: list[dict] = []
                self.entities: list[tuple[str, dict]] = []

            async def get_department(self, dept_id):
                return self.departments.get(dept_id)

            async def save_department(self, dept):
                self.departments[dept["id"]] = dict(dept)
                self.saved_departments.append(dict(dept))

            async def save_task(self, task):
                self.saved_tasks.append(dict(task))

            async def add_activity(self, activity):
                self.activities.append(activity)

            async def put_entity(self, type_, data, **kwargs):
                self.entities.append((type_, dict(data)))

            async def add_message(self, message):
                self.messages.append(message)

            async def thread_messages(self, thread_id, limit=500):
                return []

        repo = FakeRepo()
        dept = {
            "id": "source",
            "name": "Source",
            "agentName": "Source Agent",
            "state": "review",
            "currentTaskId": "task_1",
        }
        task = {
            "id": "task_1",
            "title": "Need specialist",
            "detail": "Original task",
            "status": "review",
            "priority": "normal",
            "departmentId": "source",
            "progress": 0.8,
            "createdAt": 100,
            "updatedAt": 100,
            "handoffs": [],
            "log": [],
            "deliverables": [],
        }

        next_task = await engine._create_handoff_task(
            repo,
            dept,
            repo.departments["target"],
            task,
            reason="Need target context",
            kind="consult",
            now=123,
        )

        self.assertIsNotNone(next_task)
        self.assertEqual(task["status"], "blocked")
        self.assertEqual(task["waitingOn"]["dept"], "target")
        self.assertEqual(dept["state"], "handoff")
        self.assertEqual(repo.departments["target"]["state"], "working")
        self.assertEqual(repo.departments["target"]["currentTaskId"], next_task["id"])
        self.assertEqual(next_task["status"], "in_progress")
        self.assertTrue(task["handoffs"])
        self.assertEqual(task["handoffs"][0]["targetTaskId"], next_task["id"])
        self.assertTrue(any(kind == "handoff_message" for kind, _ in repo.entities))

    async def test_idle_department_resumes_current_in_progress_task(self) -> None:
        from app import engine

        class FakeRepo:
            def __init__(self) -> None:
                self.saved_tasks: list[dict] = []
                self.saved_departments: list[dict] = []
                self.activities: list[dict] = []
                self.messages: list[dict] = []

            async def save_task(self, task):
                self.saved_tasks.append(dict(task))

            async def save_department(self, dept):
                self.saved_departments.append(dict(dept))

            async def add_activity(self, activity):
                self.activities.append(activity)

            async def thread_messages(self, thread_id, limit=500):
                return []

            async def add_message(self, message):
                self.messages.append(message)

        repo = FakeRepo()
        dept = {
            "id": "dept_a",
            "name": "Dept A",
            "agentName": "Agent A",
            "state": "idle",
            "currentTaskId": "task_a",
            "mood": 0.5,
        }
        task = {
            "id": "task_a",
            "title": "Resume me",
            "status": "in_progress",
            "departmentId": "dept_a",
            "progress": 0.4,
            "createdAt": 100,
            "updatedAt": 100,
            "log": [],
            "handoffs": [],
        }

        changed = await engine._advance_department(repo, dept, [task], [dept], 456)

        self.assertTrue(changed)
        self.assertEqual(dept["state"], "working")
        self.assertEqual(dept["currentTaskId"], "task_a")
        self.assertEqual(task["status"], "in_progress")
        self.assertIn("resume จาก currentTaskId", task["log"][-1])

    def test_job_stale_threshold_uses_job_timeout_not_heartbeat_only(self) -> None:
        from app.main import _job_stale_after_ms

        settings = SimpleNamespace(engine_stale_after_s=600.0, engine_job_timeout_s=1800.0)

        self.assertEqual(_job_stale_after_ms(settings), 1_800_000)


if __name__ == "__main__":
    unittest.main()
