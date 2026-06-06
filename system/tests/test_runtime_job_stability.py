import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


class RuntimeJobStabilityTest(unittest.IsolatedAsyncioTestCase):
    async def test_requeue_stale_running_jobs_restores_only_stale_running_rows(self) -> None:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.db.base import Base
        from app.db import tables as T
        from app.db.repo import Repo

        engine_db = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        try:
            async with engine_db.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            sessionmaker = async_sessionmaker(engine_db, expire_on_commit=False)
            async with sessionmaker() as session:
                session.add_all([
                    T.Job(
                        id="job_stale",
                        kind="objective_run",
                        status="running",
                        run_after=0,
                        priority=5,
                        payload={},
                        attempts=2,
                        created_at=100,
                        updated_at=700,
                    ),
                    T.Job(
                        id="job_fresh",
                        kind="objective_run",
                        status="running",
                        run_after=0,
                        priority=5,
                        payload={},
                        attempts=1,
                        created_at=100,
                        updated_at=950,
                    ),
                    T.Job(
                        id="job_excluded",
                        kind="image_generation",
                        status="running",
                        run_after=0,
                        priority=5,
                        payload={},
                        attempts=3,
                        created_at=100,
                        updated_at=600,
                    ),
                    T.Job(
                        id="job_done",
                        kind="objective_run",
                        status="done",
                        run_after=0,
                        priority=5,
                        payload={},
                        attempts=4,
                        created_at=100,
                        updated_at=500,
                    ),
                ])
                await session.commit()

                repo = Repo(session)
                requeued = await repo.requeue_stale_running_jobs(
                    1000,
                    stale_after_ms=200,
                    exclude_kinds={"image_generation"},
                )

                self.assertEqual([item["id"] for item in requeued], ["job_stale"])
                stale = await session.get(T.Job, "job_stale")
                fresh = await session.get(T.Job, "job_fresh")
                excluded = await session.get(T.Job, "job_excluded")
                done = await session.get(T.Job, "job_done")
                self.assertEqual(stale.status, "queued")
                self.assertEqual(stale.run_after, 1000)
                self.assertEqual(stale.updated_at, 1000)
                self.assertEqual(stale.attempts, 2)
                self.assertIn("requeued stale running job", stale.last_error)
                self.assertEqual(fresh.status, "running")
                self.assertEqual(excluded.status, "running")
                self.assertEqual(done.status, "done")
        finally:
            await engine_db.dispose()

    async def test_sqlite_claim_due_jobs_does_not_double_claim_same_row(self) -> None:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.db.base import Base
        from app.db import repo as repo_module
        from app.db import tables as T
        from app.db.repo import Repo

        with tempfile.TemporaryDirectory(prefix="atrium-sqlite-claim-") as tmp:
            engine_db = create_async_engine(f"sqlite+aiosqlite:///{Path(tmp) / 'claim.db'}", future=True)
            try:
                async with engine_db.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
                sessionmaker = async_sessionmaker(engine_db, expire_on_commit=False)
                async with sessionmaker() as session:
                    session.add(T.Job(
                        id="job_once",
                        kind="objective_run",
                        status="queued",
                        run_after=0,
                        priority=5,
                        payload={},
                        attempts=0,
                        created_at=100,
                        updated_at=100,
                    ))
                    await session.commit()

                async def claim_once() -> list[str]:
                    async with sessionmaker() as session:
                        claimed = await Repo(session).claim_due_jobs(1000, limit=1)
                        await session.commit()
                        return [job.id for job in claimed]

                with mock.patch.object(repo_module, "get_settings", return_value=SimpleNamespace(is_postgres=False)):
                    first, second = await asyncio.gather(claim_once(), claim_once())

                claimed_ids = first + second
                self.assertEqual(claimed_ids.count("job_once"), 1)
                async with sessionmaker() as session:
                    row = await session.get(T.Job, "job_once")
                    self.assertEqual(row.status, "running")
            finally:
                await engine_db.dispose()

    async def test_job_runtime_summary_reports_high_attempt_jobs_without_capping(self) -> None:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.db.base import Base
        from app.db import tables as T
        from app.db.repo import Repo

        engine_db = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        try:
            async with engine_db.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            sessionmaker = async_sessionmaker(engine_db, expire_on_commit=False)
            async with sessionmaker() as session:
                session.add_all([
                    T.Job(
                        id="job_retry_queued",
                        kind="chat_reply",
                        status="queued",
                        run_after=1200,
                        priority=5,
                        payload={},
                        attempts=7,
                        last_error="runtime unavailable",
                        created_at=100,
                        updated_at=800,
                    ),
                    T.Job(
                        id="job_retry_running",
                        kind="objective_run",
                        status="running",
                        run_after=0,
                        priority=5,
                        payload={},
                        attempts=5,
                        last_error="retry later",
                        created_at=100,
                        updated_at=700,
                    ),
                    T.Job(
                        id="job_low_attempt",
                        kind="objective_run",
                        status="queued",
                        run_after=900,
                        priority=5,
                        payload={},
                        attempts=4,
                        created_at=100,
                        updated_at=850,
                    ),
                    T.Job(
                        id="job_done_high_attempt",
                        kind="objective_run",
                        status="done",
                        run_after=0,
                        priority=5,
                        payload={},
                        attempts=9,
                        created_at=100,
                        updated_at=600,
                    ),
                ])
                await session.commit()

                summary = await Repo(session).job_runtime_summary(
                    1000,
                    stale_after_ms=1000,
                    retry_visibility_attempts=5,
                )

                self.assertEqual(summary["retryVisibility"], {
                    "visibilityOnly": True,
                    "thresholdAttempts": 5,
                    "activeJobCount": 2,
                })
                self.assertEqual(
                    [job["id"] for job in summary["highAttemptJobs"]],
                    ["job_retry_queued", "job_retry_running"],
                )
                self.assertEqual(summary["highAttemptJobs"][0]["attempts"], 7)
                self.assertEqual(summary["highAttemptJobs"][1]["status"], "running")

                retry_queued = await session.get(T.Job, "job_retry_queued")
                retry_running = await session.get(T.Job, "job_retry_running")
                self.assertEqual(retry_queued.status, "queued")
                self.assertEqual(retry_queued.attempts, 7)
                self.assertEqual(retry_running.status, "running")
                self.assertEqual(retry_running.attempts, 5)
        finally:
            await engine_db.dispose()

    async def test_engine_reaper_records_activity_and_uses_conservative_window(self) -> None:
        from app import engine

        class FakeRepo:
            def __init__(self) -> None:
                self.calls: list[dict] = []
                self.activities: list[dict] = []

            async def requeue_stale_running_jobs(self, now, **kwargs):
                self.calls.append({"now": now, **kwargs})
                return [{"id": "job_1", "kind": "objective_run", "ageMs": 5000, "attempts": 1}]

            async def add_activity(self, ev):
                self.activities.append(ev)

        repo = FakeRepo()
        settings = SimpleNamespace(
            engine_stale_after_s=10,
            engine_job_timeout_s=30,
            image_generation_timeout_s=600,
        )

        count = await engine._requeue_stale_running_jobs(repo, 10_000, settings)

        self.assertEqual(count, 1)
        self.assertEqual(repo.calls[0]["stale_after_ms"], 600_000)
        self.assertEqual(repo.calls[0]["exclude_kinds"], set())
        self.assertEqual(repo.activities[0]["severity"], "warning")
        self.assertEqual(repo.activities[0]["jobs"][0]["id"], "job_1")

    def test_engine_reaper_excludes_unbounded_image_jobs(self) -> None:
        from app import engine

        settings = SimpleNamespace(image_generation_timeout_s=0)

        self.assertEqual(engine._job_reaper_excluded_kinds(settings), {"image_generation"})

    async def test_image_worker_timeout_requeues_run_when_attempts_remain(self) -> None:
        from app.image_generation import handle_image_generation_worker_timeout

        class FakeRepo:
            def __init__(self) -> None:
                self.entities = {
                    ("image_generation_run", "run_1"): {
                        "id": "run_1",
                        "jobId": "img_1",
                        "status": "running",
                        "ownerDept": "exec",
                        "departmentId": "exec",
                        "threadId": "executive",
                        "messageId": "msg_img",
                        "queuedAt": 100,
                        "startedAt": 200,
                        "lastAttemptStartedAt": 300,
                        "attempts": 1,
                        "maxAttempts": 3,
                        "model": "gpt-image-2",
                        "mode": "generate",
                        "options": {},
                        "prompt": "draw",
                    }
                }
                self.messages = {
                    "msg_img": {
                        "id": "msg_img",
                        "threadId": "executive",
                        "text": "old",
                        "ts": 100,
                    }
                }
                self.activities: list[dict] = []

            async def get_entity(self, etype, eid):
                return dict(self.entities.get((etype, eid)) or {})

            async def put_entity(self, etype, obj, **kwargs):
                del kwargs
                self.entities[(etype, obj["id"])] = dict(obj)
                return obj

            async def get_department(self, dept_id):
                return {"id": dept_id, "name": "Executive", "agentName": "Executive AI"}

            async def get_message(self, msg_id, *, thread_id=None):
                msg = self.messages.get(msg_id)
                if msg and (thread_id is None or msg.get("threadId") == thread_id):
                    return dict(msg)
                return None

            async def update_message(self, msg):
                self.messages[msg["id"]] = dict(msg)

            async def add_message(self, msg):
                self.messages[msg["id"]] = dict(msg)

            async def add_activity(self, ev):
                self.activities.append(dict(ev))

        repo = FakeRepo()

        result = await handle_image_generation_worker_timeout(
            repo,
            {"runId": "run_1", "jobId": "img_1", "departmentId": "exec"},
            "TimeoutError: image job exceeded 1s",
        )

        run = repo.entities[("image_generation_run", "run_1")]
        self.assertEqual(result["status"], "retry_queued")
        self.assertEqual(result["delayMs"], 60_000)
        self.assertEqual(run["status"], "queued")
        self.assertEqual(run["attempts"], 1)
        self.assertTrue(run["workerTimeoutRetry"])
        self.assertGreater(run["retryAfter"], 0)
        self.assertIn("timeout", run["lastError"].lower())
        self.assertIn("จะลองใหม่", repo.messages["msg_img"]["text"])
        self.assertEqual(repo.activities[0]["severity"], "warn")

    async def test_image_worker_timeout_branch_requeues_job_instead_of_failing_when_retryable(self) -> None:
        from app import engine

        class FakeSessionScope:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb):
                del exc_type, exc, tb
                return False

        fake_repo = SimpleNamespace(mark_job=mock.AsyncMock())

        async def never_finishes(repo, job, now):
            del repo, job, now
            await asyncio.sleep(1)

        with (
            mock.patch.object(engine, "session_scope", return_value=FakeSessionScope()),
            mock.patch.object(engine, "Repo", return_value=fake_repo),
            mock.patch.object(engine, "_process_due_job", side_effect=never_finishes),
            mock.patch.object(
                engine,
                "handle_image_generation_worker_timeout",
                new=mock.AsyncMock(return_value={"status": "retry_queued", "delayMs": 12_000}),
            ) as timeout_handler,
        ):
            processed = await engine._process_claimed_image_generation_record(
                SimpleNamespace(id="job_1", kind="image_generation", payload={"runId": "run_1"}),
                SimpleNamespace(image_generation_timeout_s=0.01),
            )

        self.assertEqual(processed, 0)
        timeout_handler.assert_awaited_once()
        self.assertEqual(fake_repo.mark_job.await_args.args[:2], ("job_1", "queued"))
        self.assertGreaterEqual(fake_repo.mark_job.await_args.kwargs["run_after"], 0)
        self.assertIn("TimeoutError", fake_repo.mark_job.await_args.kwargs["error"])

    async def test_image_generation_job_skips_completed_run_without_regenerating(self) -> None:
        from app import image_generation

        artifact = {
            "id": "artifact_img_1",
            "name": "done.png",
            "contentMime": "image/png",
            "uri": "data:image/png;base64,AAAA",
        }

        class FakeRepo:
            def __init__(self) -> None:
                self.entities = {
                    ("image_generation_run", "run_done"): {
                        "id": "run_done",
                        "jobId": "img_done",
                        "status": "succeeded",
                        "ownerDept": "exec",
                        "projectId": None,
                        "mode": "generate",
                        "model": "gpt-image-2",
                        "request": {"prompt": "draw"},
                        "artifacts": [artifact],
                        "artifactIds": ["artifact_img_1"],
                        "locations": [{"artifactId": "artifact_img_1", "url": "/api/artifacts/artifact_img_1"}],
                        "usage": {"provider": "chatgpt_account"},
                        "requestId": "req_1",
                        "summary": "already done",
                        "idempotencySkipCount": 0,
                    }
                }
                self.activities: list[dict] = []

            async def get_entity(self, etype, eid):
                return dict(self.entities.get((etype, eid)) or {})

            async def put_entity(self, etype, obj, **kwargs):
                del kwargs
                self.entities[(etype, obj["id"])] = dict(obj)

            async def add_activity(self, activity):
                self.activities.append(dict(activity))

        repo = FakeRepo()
        with mock.patch.object(
            image_generation,
            "generate_image_assets",
            new=mock.AsyncMock(side_effect=AssertionError("provider should not be called")),
        ) as generate:
            result = await image_generation.process_image_generation_job(
                repo,
                {"runId": "run_done", "jobId": "img_done", "departmentId": "exec"},
                now=1234,
            )

        generate.assert_not_awaited()
        self.assertTrue(result["ok"])
        self.assertTrue(result["idempotency"]["skippedCompletedRun"])
        self.assertEqual(result["artifacts"], [artifact])
        run = repo.entities[("image_generation_run", "run_done")]
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(run["idempotencySkipCount"], 1)
        self.assertEqual(run["lastIdempotencySkipAt"], 1234)
        self.assertIn("ข้ามการสร้างภาพซ้ำ", repo.activities[0]["detail"])

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
        self.assertEqual(seen["exclude_kinds"], {"chat_reply", "image_generation", "trigger_run"})

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

    async def test_objective_run_uses_deterministic_task_id_and_skips_duplicate_side_effects(self) -> None:
        from app import engine

        class FakeRepo:
            def __init__(self) -> None:
                self.tasks: dict[str, dict] = {}
                self.activities: list[dict] = []
                self.notifications: list[dict] = []

            async def list_departments(self):
                return []

            async def get_department(self, dept_id):
                return {"id": dept_id, "name": "Research", "agentName": "Research AI"}

            async def get_task(self, task_id):
                return self.tasks.get(task_id)

            async def get_entity(self, etype, eid):
                del etype, eid
                return None

            async def save_task(self, task):
                self.tasks[task["id"]] = dict(task)

            async def add_activity(self, activity):
                self.activities.append(activity)

            async def put_entity(self, type_, data, **kwargs):
                del kwargs
                if type_ == "notification":
                    self.notifications.append(dict(data))

        repo = FakeRepo()
        job = SimpleNamespace(
            kind="objective_run",
            payload={
                "objectiveId": "obj_1",
                "title": "Daily research",
                "departmentId": "research",
                "cadence": "daily",
                "scheduledFor": 123,
            },
        )

        await engine._process_due_job(repo, job, 456)
        await engine._process_due_job(repo, job, 789)

        self.assertEqual(len(repo.tasks), 1)
        task = next(iter(repo.tasks.values()))
        self.assertEqual(task["id"], engine._scheduled_task_id("objective_run", source_id="obj_1", dept_id="research", scheduled_for=123))
        self.assertIn(f"objective idempotencyKey={task['id']}", task["log"])
        self.assertEqual(len(repo.activities), 1)
        self.assertEqual(len(repo.notifications), 1)

    async def test_trigger_run_uses_deterministic_task_id_per_assignee_and_skips_duplicates(self) -> None:
        from app import engine

        class FakeRepo:
            def __init__(self) -> None:
                self.departments = {
                    "dept_a": {"id": "dept_a", "name": "A", "agentName": "A AI"},
                    "dept_b": {"id": "dept_b", "name": "B", "agentName": "B AI"},
                }
                self.tasks: dict[str, dict] = {}
                self.activities: list[dict] = []
                self.notifications: list[dict] = []

            async def list_departments(self):
                return list(self.departments.values())

            async def get_department(self, dept_id):
                return self.departments.get(dept_id)

            async def get_task(self, task_id):
                return self.tasks.get(task_id)

            async def get_entity(self, etype, eid):
                del etype, eid
                return None

            async def save_task(self, task):
                self.tasks[task["id"]] = dict(task)

            async def add_activity(self, activity):
                self.activities.append(activity)

            async def put_entity(self, type_, data, **kwargs):
                del kwargs
                if type_ == "notification":
                    self.notifications.append(dict(data))

        repo = FakeRepo()
        job = SimpleNamespace(
            kind="trigger_run",
            payload={
                "triggerId": "trig_1",
                "title": "Budget review",
                "target": "dept:dept_a",
                "event": "budget",
                "scheduledFor": 321,
            },
        )

        await engine._process_due_job(repo, job, 456)
        await engine._process_due_job(repo, job, 789)

        self.assertEqual(len(repo.tasks), 1)
        task = next(iter(repo.tasks.values()))
        self.assertEqual(task["id"], engine._scheduled_task_id("trigger_run", source_id="trig_1", dept_id="dept_a", scheduled_for=321, event="budget"))
        self.assertIn(f"trigger idempotencyKey={task['id']}", task["log"])
        self.assertEqual(len(repo.activities), 1)
        self.assertEqual(len(repo.notifications), 1)

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

    def test_worker_concurrency_defaults_and_bounds(self) -> None:
        from app import engine

        self.assertEqual(engine._bounded_worker_concurrency(None), 8)
        self.assertEqual(engine._bounded_worker_concurrency(0), 8)
        self.assertEqual(engine._bounded_worker_concurrency(5), 8)
        self.assertEqual(engine._bounded_worker_concurrency("12"), 12)
        self.assertEqual(engine._bounded_worker_concurrency(50), 50)
        self.assertEqual(engine._bounded_worker_concurrency(99), 50)

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

    async def test_chat_reply_timeout_marks_pending_message_failed(self) -> None:
        from app import engine

        class FakeRepo:
            def __init__(self) -> None:
                self.messages = {
                    "reply_1": {
                        "id": "reply_1",
                        "threadId": "thread_1",
                        "role": "agent",
                        "authorName": "Agent",
                        "text": "",
                        "pending": True,
                        "status": "sending",
                        "replyToMessageId": "user_1",
                        "ts": 100,
                    }
                }

            async def get_message(self, msg_id, *, thread_id=None):
                msg = self.messages.get(msg_id)
                if msg and (thread_id is None or msg.get("threadId") == thread_id):
                    return dict(msg)
                return None

            async def update_message(self, msg):
                self.messages[msg["id"]] = dict(msg)

            async def add_message(self, msg):
                self.messages[msg["id"]] = dict(msg)

        repo = FakeRepo()
        with mock.patch.object(engine.hub, "pulse") as pulse:
            await engine._mark_chat_reply_timeout(
                repo,
                {"threadId": "thread_1", "replyMessageId": "reply_1", "userMessageId": "user_1"},
                timeout_s=1.5,
                now=123,
            )

        reply = repo.messages["reply_1"]
        self.assertFalse(reply["pending"])
        self.assertEqual(reply["status"], "failed")
        self.assertEqual(reply["ts"], 123)
        self.assertEqual(reply["completedAt"], 123)
        self.assertEqual(reply["error"]["code"], "chat_reply_timeout")
        self.assertTrue(reply["error"]["retryable"])
        self.assertTrue(reply["text"])
        pulse.assert_called_once()
        self.assertEqual(pulse.call_args.args[0]["kind"], "msg_done")

    async def test_claimed_chat_reply_uses_short_chat_timeout(self) -> None:
        from contextlib import asynccontextmanager
        from app import engine

        job = SimpleNamespace(
            id="job_chat_timeout",
            kind="chat_reply",
            payload={"threadId": "thread_1", "replyMessageId": "reply_1"},
        )

        class FakeRepo:
            def __init__(self) -> None:
                self.mark_job = mock.AsyncMock()

        repo = FakeRepo()

        @asynccontextmanager
        async def fake_session_scope():
            yield object()

        async def fake_wait_for(awaitable, timeout):
            awaitable.close()
            self.assertEqual(timeout, 2.5)
            raise asyncio.TimeoutError()

        settings = SimpleNamespace(engine_job_timeout_s=99.0, chat_reply_timeout_s=2.5)
        with (
            mock.patch.object(engine, "session_scope", fake_session_scope),
            mock.patch.object(engine, "Repo", return_value=repo),
            mock.patch.object(engine.asyncio, "wait_for", side_effect=fake_wait_for),
            mock.patch.object(engine, "_handle_job_timeout", new=mock.AsyncMock(return_value={"action": "fail"})) as timeout_handler,
            mock.patch.object(engine, "now_ms", return_value=123),
        ):
            processed = await engine._process_claimed_job_record(job, settings)

        self.assertEqual(processed, 0)
        timeout_handler.assert_awaited_once()
        self.assertEqual(timeout_handler.await_args.kwargs["timeout_s"], 2.5)
        repo.mark_job.assert_awaited_with("job_chat_timeout", "failed", error="TimeoutError: job exceeded 2.5s")

    async def test_non_chat_job_timeout_requeues_and_records_recovery(self) -> None:
        from app import engine

        job = SimpleNamespace(
            id="job_timeout",
            kind="compact_dept",
            payload={"departmentId": "dept_a", "secret": "do-not-store"},
        )

        class FakeRepo:
            def __init__(self) -> None:
                self.s = object()
                self.mark_job = mock.AsyncMock()
                self.entities: list[dict] = []
                self.activities: list[dict] = []

            async def due_jobs(self, now, *, limit, kind=None, exclude_kinds=None):
                del now, limit, kind, exclude_kinds
                return [job]

            async def put_entity(self, etype, data, **kwargs):
                self.entities.append({"type": etype, "data": dict(data), "kwargs": dict(kwargs)})

            async def add_activity(self, activity):
                self.activities.append(dict(activity))

        async def timeout_process(repo, claimed_job, now):
            del repo, claimed_job, now
            raise asyncio.TimeoutError()

        repo = FakeRepo()
        settings = SimpleNamespace(engine_job_timeout_s=1.0, engine_timeout_retry_delay_s=2.5)
        with (
            mock.patch.object(engine, "commit_and_release", new=mock.AsyncMock()),
            mock.patch.object(engine, "_process_due_job", side_effect=timeout_process),
            mock.patch.object(engine, "now_ms", return_value=10_000),
        ):
            processed = await engine._process_due_jobs(repo, 1000, settings, kind="compact_dept")

        self.assertEqual(processed, 0)
        self.assertEqual(repo.mark_job.await_args_list[0].args, ("job_timeout", "running"))
        queued_call = repo.mark_job.await_args_list[-1]
        self.assertEqual(queued_call.args[:2], ("job_timeout", "queued"))
        self.assertEqual(queued_call.kwargs["run_after"], 12_500)
        self.assertIn("TimeoutError", queued_call.kwargs["error"])
        self.assertEqual(repo.entities[0]["type"], "job_timeout_recovery")
        recovery = repo.entities[0]["data"]
        self.assertEqual(recovery["jobStatusAfter"], "queued")
        self.assertEqual(recovery["retryDelayMs"], 2500)
        self.assertEqual(recovery["payload"], {"departmentId": "dept_a"})
        self.assertTrue(recovery["fullAutonomyPreserved"])
        self.assertEqual(repo.activities[0]["severity"], "warn")

    async def test_chat_context_token_source_labels_provider_timeout(self) -> None:
        from app import engine

        class TimeoutProvider:
            async def count_context_tokens(self, **kwargs):
                del kwargs
                raise asyncio.TimeoutError()

        with mock.patch.object(engine, "get_provider", return_value=TimeoutProvider()):
            tokens, source = await engine._chat_context_tokens_for_turn(
                {"id": "dept_a", "providerId": "mock", "model": "mock-model"},
                "system prompt",
                [],
                {"id": "msg_user", "role": "user", "text": "hello"},
            )

        self.assertGreater(tokens, 0)
        self.assertEqual(source, "estimate:provider_timeout")

    async def test_chat_context_token_source_labels_provider_error(self) -> None:
        from app import engine

        class ErrorProvider:
            async def count_context_tokens(self, **kwargs):
                del kwargs
                raise RuntimeError("counter unavailable")

        with mock.patch.object(engine, "get_provider", return_value=ErrorProvider()):
            tokens, source = await engine._chat_context_tokens_for_turn(
                {"id": "dept_a", "providerId": "mock", "model": "mock-model"},
                "system prompt",
                [],
                {"id": "msg_user", "role": "user", "text": "hello"},
            )

        self.assertGreater(tokens, 0)
        self.assertEqual(source, "estimate:provider_error:RuntimeError")

    def test_json_object_parser_returns_visibility_metadata(self) -> None:
        from app import engine

        parsed, meta = engine._parse_json_object_with_meta("not json at all")
        self.assertEqual(parsed, {})
        self.assertFalse(meta["ok"])
        self.assertEqual(meta["error"], "no_json_object")
        self.assertIn("firstError", meta)

        parsed, meta = engine._parse_json_object_with_meta("prefix\n{\"status\":\"ok\"}\n")
        self.assertEqual(parsed, {"status": "ok"})
        self.assertTrue(meta["ok"])
        self.assertEqual(meta["source"], "object_substring")

    async def test_suppressed_engine_error_records_activity(self) -> None:
        from app import engine

        class FakeRepo:
            def __init__(self) -> None:
                self.activities: list[dict] = []

            async def add_activity(self, activity):
                self.activities.append(activity)

        repo = FakeRepo()
        await engine._record_suppressed_engine_error(repo, "telegram_progress.test", RuntimeError("boom"), now=123)

        self.assertEqual(len(repo.activities), 1)
        activity = repo.activities[0]
        self.assertEqual(activity["severity"], "warn")
        self.assertIn("telegram_progress.test", activity["text"])
        self.assertIn("RuntimeError", activity["text"])

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

    async def test_handoff_task_waits_source_and_wakes_idle_target(self) -> None:
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

            async def put_entity(self, type_, data, **kwargs):
                self.entities.append((type_, dict(data)))

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
            "draftDeliverableMarkdown": "Source draft v0.9",
        }

        tmpdir = tempfile.TemporaryDirectory(prefix="atrium-handoff-packet-")
        self.addCleanup(tmpdir.cleanup)
        tmp = tmpdir.name

        class FakeSettings:
            max_handoff_depth = 5
            max_consult_rounds = 3

            @property
            def workspace_dir(self):
                path = Path(tmp) / "workspace"
                path.mkdir(parents=True, exist_ok=True)
                return path

        with mock.patch.object(engine, "get_settings", return_value=FakeSettings()):
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
        self.assertEqual(task["status"], "waiting")
        self.assertEqual(task["waitingOn"]["dept"], "target")
        self.assertEqual(task["waitingOn"]["reason"], engine.HANDOFF_WAITING_REPLY_REASON)
        self.assertEqual(dept["state"], "handoff")
        self.assertEqual(repo.departments["target"]["state"], "working")
        self.assertEqual(repo.departments["target"]["currentTaskId"], next_task["id"])
        self.assertEqual(next_task["status"], "in_progress")
        self.assertTrue(task["handoffs"])
        self.assertEqual(task["handoffs"][0]["targetTaskId"], next_task["id"])
        self.assertEqual(task["handoffs"][0]["status"], "requested")
        self.assertTrue(task["handoffs"][0]["chainId"])
        self.assertGreater(task["handoffs"][0]["deadlineAt"], 123)
        self.assertTrue(any(kind == "handoff_message" for kind, _ in repo.entities))
        artifacts = [data for kind, data in repo.entities if kind == "artifact"]
        versions = [data for kind, data in repo.entities if kind == "artifact_version"]
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(len(versions), 1)
        artifact = artifacts[0]
        version = versions[0]
        self.assertEqual(artifact["kind"], "report")
        self.assertEqual(artifact["version"], 1)
        self.assertIn("handoff_packet", artifact["tags"])
        self.assertEqual(artifact["taskIds"], ["task_1", next_task["id"]])
        self.assertIn(artifact["id"], task["deliverables"])
        self.assertIn(artifact["id"], next_task["deliverables"])
        self.assertEqual(task["handoffs"][0]["contextPacketArtifactId"], artifact["id"])
        self.assertEqual(task["handoffs"][0]["contextPacketArtifactVersion"], 1)
        self.assertEqual(version["artifactId"], artifact["id"])
        self.assertTrue(artifact["uri"].endswith("_v1.md"))
        packet_path = Path(artifact["uri"])
        self.assertTrue(packet_path.is_file())
        packet_text = packet_path.read_text(encoding="utf-8")
        self.assertIn("# Handoff Packet v1", packet_text)
        self.assertIn("## Minimum Context", packet_text)
        self.assertIn("## Retrieval Manifest", packet_text)
        self.assertIn(f"/api/artifacts/{artifact['id']}/content", packet_text)
        self.assertIn(f"/api/artifacts/{artifact['id']}/download", packet_text)
        self.assertIn("งานนี้คือ:", packet_text)
        self.assertIn("Need target context", packet_text)
        self.assertIn("Source draft v0.9", packet_text)
        threads = {message["threadId"] for message in repo.messages}
        self.assertIn("executive", threads)
        self.assertIn("dept:source", threads)
        self.assertIn("dept:target", threads)
        self.assertTrue(any(
            (message.get("input") or {}).get("visibilityEvent") == "handoff_requested"
            for message in repo.messages
        ))

    async def test_task_payload_inlines_paged_artifact_context(self) -> None:
        from app import engine

        tmpdir = tempfile.TemporaryDirectory(prefix="atrium-task-context-")
        self.addCleanup(tmpdir.cleanup)
        artifact_path = Path(tmpdir.name) / "long.md"
        page_chars = engine.TASK_ARTIFACT_CONTEXT_PAGE_CHARS
        artifact_path.write_text("A" * page_chars + "SECOND_PAGE_MARKER\n" + "B" * 100, encoding="utf-8")

        class FakeRepo:
            async def get_entity(self, type_, entity_id):
                if type_ == "artifact" and entity_id == "art_long":
                    return {
                        "id": "art_long",
                        "name": "Long context",
                        "kind": "report",
                        "status": "approved",
                        "version": 1,
                        "ownerDept": "source",
                        "uri": str(artifact_path),
                        "storage": "filesystem",
                        "contentMime": "text/markdown; charset=utf-8",
                        "preview": {"kind": "md", "uri": str(artifact_path)},
                        "taskIds": ["task_1"],
                    }
                return None

        task = {
            "id": "task_1",
            "title": "Read artifact context",
            "detail": "Use the attached report",
            "status": "in_progress",
            "priority": "normal",
            "progress": 0.2,
            "log": [],
            "handoffs": [],
            "waitingOn": None,
            "deliverables": ["art_long"],
            "contextPaging": {"artifactPages": {"art_long": 1}},
        }

        payload, next_paging = await engine._task_payload_with_context(FakeRepo(), task)

        context = payload["artifactContext"]
        self.assertEqual(context["mode"], "paged_artifact_manifest")
        self.assertEqual(context["artifactIds"], ["art_long"])
        entry = context["artifacts"][0]
        self.assertEqual(entry["contentStatus"], "available")
        self.assertEqual(entry["pageIndex"], 1)
        self.assertIn("SECOND_PAGE_MARKER", entry["excerpt"]["text"])
        self.assertEqual(entry["contentApi"], "/api/artifacts/art_long/content")
        self.assertEqual(entry["downloadApi"], "/api/artifacts/art_long/download")
        self.assertGreater(entry["contentSizeBytes"], 0)
        self.assertRegex(entry["contentHash"], r"^[0-9a-f]{64}$")
        self.assertEqual(next_paging["artifactPages"]["art_long"], 0)

    async def test_task_payload_marks_empty_placeholder_artifact(self) -> None:
        from app import engine

        class FakeRepo:
            async def get_entity(self, type_, entity_id):
                if type_ == "artifact" and entity_id == "art_empty":
                    return {
                        "id": "art_empty",
                        "name": "Empty placeholder",
                        "kind": "memo",
                        "status": "draft",
                        "version": 1,
                        "ownerDept": "source",
                        "uri": "atrium://artifact/art_empty",
                        "preview": None,
                        "taskIds": ["task_1"],
                    }
                return None

        task = {
            "id": "task_1",
            "title": "Do not treat placeholders as evidence",
            "detail": "Task detail is still available",
            "status": "in_progress",
            "priority": "normal",
            "progress": 0.1,
            "log": [],
            "handoffs": [],
            "waitingOn": None,
            "deliverables": ["art_empty"],
        }

        payload, _ = await engine._task_payload_with_context(FakeRepo(), task)

        context = payload["artifactContext"]
        entry = context["artifacts"][0]
        self.assertEqual(entry["contentStatus"], "empty")
        self.assertIn("art_empty", context["emptyArtifactIds"])
        self.assertEqual(entry["contentApi"], "/api/artifacts/art_empty/content")

    async def test_work_status_notice_routes_and_dedupes(self) -> None:
        from app.work_visibility import emit_work_status_notice

        class FakeRepo:
            def __init__(self) -> None:
                self.messages: list[dict] = []

            async def add_message(self, message):
                self.messages.append(message)

            async def thread_messages(self, thread_id, limit=500):
                return [message for message in self.messages if message.get("threadId") == thread_id]

        repo = FakeRepo()
        source = {"id": "source", "name": "Source", "agentName": "Source Agent"}
        target = {"id": "target", "name": "Target", "agentName": "Target Agent"}
        task = {"id": "task_1", "title": "Visible task", "status": "in_progress", "progress": 0.5}

        first = await emit_work_status_notice(
            repo,
            event="task_assigned",
            summary="Source ส่งงานให้ Target",
            source_dept=source,
            target_dept=target,
            task=task,
            dedupe_key="same-key",
            now=123,
        )
        second = await emit_work_status_notice(
            repo,
            event="task_assigned",
            summary="Source ส่งงานให้ Target",
            source_dept=source,
            target_dept=target,
            task=task,
            dedupe_key="same-key",
            now=124,
        )

        self.assertEqual(len(first), 3)
        self.assertEqual(len(second), 0)
        self.assertEqual({message["threadId"] for message in repo.messages}, {"executive", "dept:source", "dept:target"})
        self.assertEqual({(message.get("flow") or {}).get("kind") for message in repo.messages}, {"department_work"})

    async def test_work_status_notice_dedupes_after_thread_window_rolls_off(self) -> None:
        from app.work_visibility import emit_work_status_notice

        class FakeRepo:
            def __init__(self) -> None:
                self.messages: list[dict] = []
                self.entities: dict[tuple[str, str], dict] = {}

            async def add_message(self, message):
                self.messages.append(message)

            async def thread_messages(self, thread_id, limit=120):
                matches = [message for message in self.messages if message.get("threadId") == thread_id]
                return matches[-limit:]

            async def get_entity(self, etype, eid):
                return self.entities.get((etype, eid))

            async def put_entity(self, etype, obj, **kwargs):
                del kwargs
                self.entities[(etype, obj["id"])] = dict(obj)

        repo = FakeRepo()
        source = {"id": "source", "name": "Source", "agentName": "Source Agent"}
        task = {"id": "task_1", "title": "Visible task", "status": "in_progress", "progress": 0.5}

        first = await emit_work_status_notice(
            repo,
            event="task_started",
            summary="Source เริ่มงาน",
            source_dept=source,
            task=task,
            dedupe_key="long-thread-key",
            include_executive=False,
            now=123,
        )
        for index in range(150):
            repo.messages.append({
                "id": f"msg_filler_{index}",
                "threadId": "dept:source",
                "text": "filler",
                "input": {},
            })
        second = await emit_work_status_notice(
            repo,
            event="task_started",
            summary="Source เริ่มงาน",
            source_dept=source,
            task=task,
            dedupe_key="long-thread-key",
            include_executive=False,
            now=124,
        )

        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 0)
        self.assertEqual(
            [message for message in repo.messages if (message.get("input") or {}).get("visibilityEventKey")],
            first,
        )

    async def test_idle_department_resumes_current_in_progress_task(self) -> None:
        from app import engine

        class FakeRepo:
            def __init__(self) -> None:
                self.saved_tasks: list[dict] = []
                self.saved_departments: list[dict] = []
                self.activities: list[dict] = []
                self.messages: list[dict] = []
                self.entities: list[tuple[str, dict]] = []

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

            async def put_entity(self, type_, data, **kwargs):
                self.entities.append((type_, dict(data)))

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

    async def test_idle_autonomy_roll_waits_for_department_interval(self) -> None:
        from app import engine

        class FakeRepo:
            def __init__(self) -> None:
                self.saved_departments: list[dict] = []

            async def save_department(self, dept):
                self.saved_departments.append(dict(dept))

        dept = {
            "id": "dept_auto",
            "name": "Auto Dept",
            "agentName": "Auto Agent",
            "state": "idle",
            "currentTaskId": None,
            "autonomy": True,
        }
        repo = FakeRepo()

        with mock.patch.object(engine, "_llm_autonomous_task", new=mock.AsyncMock()) as llm_task:
            changed = await engine._advance_department(repo, dept, [], [dept], 1_000)

        self.assertFalse(changed)
        llm_task.assert_not_awaited()
        self.assertEqual(len(repo.saved_departments), 1)
        schedule = repo.saved_departments[-1]["autonomySchedule"]
        self.assertEqual(schedule["lastRollAt"], 1_000)
        self.assertEqual(schedule["nextRollAt"], 16_000)
        self.assertEqual(schedule["chancePercent"], 10.0)

        repo.saved_departments.clear()
        changed = await engine._advance_department(repo, dept, [], [dept], 15_999)

        self.assertFalse(changed)
        self.assertEqual(repo.saved_departments, [])

    async def test_idle_autonomy_rolls_every_15_seconds_with_hourly_chance_increase_and_reset(self) -> None:
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
                self.activities.append(dict(activity))

            async def thread_messages(self, thread_id, limit=500):
                return []

            async def add_message(self, message):
                self.messages.append(dict(message))

        dept = {
            "id": "dept_auto",
            "name": "Auto Dept",
            "agentName": "Auto Agent",
            "state": "idle",
            "currentTaskId": None,
            "autonomy": True,
            "autonomySchedule": {
                "idleSinceAt": 0,
                "lastRollAt": 3_600_000,
                "chancePercent": 10.0,
            },
        }
        repo = FakeRepo()

        with (
            mock.patch.object(engine.random, "random", return_value=0.15),
            mock.patch.object(
                engine,
                "_llm_autonomous_task",
                new=mock.AsyncMock(return_value={
                    "title": "Proactive check",
                    "detail": "Check a useful area",
                    "priority": "normal",
                    "whyNow": "ไม่มีงานค้างและถึงรอบตรวจคุณภาพรายชั่วโมง",
                    "needsApproval": False,
                }),
            ),
        ):
            changed = await engine._advance_department(repo, dept, [], [dept], 3_615_000)

        self.assertTrue(changed)
        self.assertEqual(len(repo.saved_tasks), 1)
        task = repo.saved_tasks[-1]
        self.assertEqual(task["autonomyTrace"]["rollIntervalMs"], 15_000)
        self.assertEqual(task["autonomyTrace"]["chancePercent"], 16.0)
        self.assertEqual(task["autonomyTrace"]["idleHours"], 1)
        reset_schedule = repo.saved_departments[-1]["autonomySchedule"]
        self.assertEqual(reset_schedule["chancePercent"], 5.0)
        self.assertTrue(reset_schedule["resetChancePending"])
        self.assertTrue(any("ยกเว้นกรณีผู้บริหารเป็นคนเริ่มงานใหม่นั้นขึ้นมาเอง" in message.get("text", "") for message in repo.messages))

    async def test_idle_autonomy_first_roll_after_work_reset_uses_five_percent(self) -> None:
        from app import engine

        dept = {
            "id": "dept_auto",
            "name": "Auto Dept",
            "agentName": "Auto Agent",
            "state": "idle",
            "currentTaskId": None,
            "autonomy": True,
            "autonomySchedule": {
                "trigger": "idle_department_autonomy",
                "status": "reset_on_work",
                "resetAt": 100,
                "resetChancePending": True,
                "chance": 0.05,
                "chancePercent": 5.0,
                "rollIntervalMs": 15_000,
            },
        }

        due, chance, schedule, changed = engine._prepare_autonomy_idle_roll(dept, 200)

        self.assertFalse(due)
        self.assertTrue(changed)
        self.assertEqual(chance, 0.05)
        self.assertEqual(schedule["chancePercent"], 5.0)
        self.assertEqual(schedule["nextRollAt"], 15_200)

    async def test_department_work_step_preserves_concurrent_task_edits(self) -> None:
        from app import engine

        class FakeRepo:
            def __init__(self, current_task: dict) -> None:
                self.current_task = dict(current_task)
                self.saved_tasks: list[dict] = []
                self.saved_departments: list[dict] = []
                self.activities: list[dict] = []

            async def get_task_fresh(self, task_id):
                if self.current_task.get("id") == task_id:
                    return dict(self.current_task)
                return None

            async def save_task(self, task):
                self.current_task = dict(task)
                self.saved_tasks.append(dict(task))

            async def save_department(self, dept):
                self.saved_departments.append(dict(dept))

            async def add_activity(self, activity):
                self.activities.append(activity)

        base_task = {
            "id": "task_a",
            "title": "Original title",
            "detail": "old detail",
            "status": "in_progress",
            "departmentId": "dept_a",
            "priority": "normal",
            "progress": 0.2,
            "createdAt": 100,
            "updatedAt": 100,
            "log": ["created"],
            "handoffs": [],
        }
        current_task = {
            **base_task,
            "detail": "owner edited detail",
            "priority": "urgent",
            "progress": 0.55,
            "updatedAt": 300,
            "log": ["created", "owner edited task"],
        }
        repo = FakeRepo(current_task)
        dept = {
            "id": "dept_a",
            "name": "Dept A",
            "agentName": "Agent A",
            "state": "working",
            "currentTaskId": "task_a",
            "mood": 0.5,
        }

        with (
            mock.patch.object(
                engine,
                "_llm_work_step",
                new=mock.AsyncMock(return_value={"status": "in_progress", "log": "engine progress", "progressDelta": 0.2}),
            ),
            mock.patch.object(engine, "_add_executive_watch_line", new=mock.AsyncMock()),
        ):
            changed = await engine._advance_department(repo, dept, [dict(base_task)], [dept], 456)

        self.assertTrue(changed)
        saved = repo.saved_tasks[-1]
        self.assertEqual(saved["detail"], "owner edited detail")
        self.assertEqual(saved["priority"], "urgent")
        self.assertEqual(saved["progress"], 0.55)
        self.assertEqual(saved["status"], "in_progress")
        self.assertEqual(saved["log"], ["created", "owner edited task", "engine progress"])

    async def test_engine_task_merge_preserves_concurrent_terminal_status(self) -> None:
        from app import engine

        base_task = {
            "id": "task_a",
            "title": "Task",
            "status": "in_progress",
            "progress": 0.4,
            "updatedAt": 100,
            "log": [],
        }
        proposed = {
            **base_task,
            "status": "review",
            "progress": 0.9,
            "updatedAt": 500,
            "log": ["engine progress"],
        }
        current = {
            **base_task,
            "status": "cancelled",
            "progress": 1,
            "updatedAt": 300,
            "log": ["owner cancelled"],
        }

        merged = engine._merge_engine_task_update(base_task, proposed, current)

        self.assertEqual(merged["status"], "cancelled")
        self.assertEqual(merged["progress"], 1)
        self.assertEqual(merged["log"], ["owner cancelled", "engine progress"])

    async def test_blocked_retry_guard_freezes_repeated_stale_blockers(self) -> None:
        from app import engine

        class FakeRepo:
            def __init__(self) -> None:
                self.saved_tasks: list[dict] = []
                self.saved_departments: list[dict] = []
                self.activities: list[dict] = []
                self.messages: list[dict] = []
                self.entities: list[tuple[str, dict]] = []

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

            async def put_entity(self, type_, data, **kwargs):
                self.entities.append((type_, dict(data)))

        repo = FakeRepo()
        dept = {
            "id": "dept_blocked",
            "name": "Blocked Dept",
            "agentName": "Blocked Agent",
            "state": "blocked",
            "currentTaskId": "task_blocked",
            "mood": 0.3,
        }
        task = {
            "id": "task_blocked",
            "title": "Needs evidence",
            "status": "blocked",
            "departmentId": "dept_blocked",
            "progress": 0.4,
            "createdAt": 100,
            "updatedAt": 100,
            "log": [
                "ตรวจซ้ำ: ยังไม่มีข้อมูลจริง คง blocked",
                "ตรวจซ้ำ: ยังไม่มีข้อมูลจริง คง blocked",
                "ตรวจซ้ำ: ยังไม่มีข้อมูลจริง คง blocked",
            ],
            "handoffs": [],
        }

        with mock.patch.object(engine.random, "random", return_value=0.0):
            changed = await engine._advance_department(repo, dept, [task], [dept], 456)

        self.assertTrue(changed)
        self.assertEqual(dept["state"], "idle")
        self.assertIsNone(dept["currentTaskId"])
        self.assertNotEqual(dept["state"], "working")
        self.assertEqual(task["blockedRetryGuard"]["status"], "frozen")
        self.assertTrue(task["blockedRetryGuard"]["executiveDecisionRequestId"].startswith("edr_"))
        self.assertEqual(task["blockedRetryGuard"]["executiveAction"], "ask_clarification")
        self.assertEqual(task["status"], "waiting")
        self.assertEqual(task["waitingOn"]["reason"], engine.HANDOFF_CLARIFICATION_REASON)
        self.assertTrue(any("หยุดปลุกอัตโนมัติ" in line for line in task["log"]))
        self.assertTrue(any(kind == "executive_decision_request" for kind, _ in repo.entities))
        self.assertTrue(repo.saved_tasks)
        self.assertTrue(any("หยุด retry" in activity["text"] for activity in repo.activities))
        self.assertTrue(any(
            message.get("threadId") == "dept:dept_blocked"
            and (message.get("input") or {}).get("visibilityEvent") == "task_blocked"
            for message in repo.messages
        ))

    async def test_executive_auto_all_actions_apply_task_state(self) -> None:
        from app import engine

        class FakeRepo:
            def __init__(self) -> None:
                self.departments = {
                    "dept_a": {
                        "id": "dept_a",
                        "name": "Dept A",
                        "agentName": "Agent A",
                        "state": "blocked",
                        "currentTaskId": "task_blocked",
                    },
                    "dept_b": {
                        "id": "dept_b",
                        "name": "Dept B",
                        "agentName": "Agent B",
                        "state": "idle",
                        "currentTaskId": None,
                    },
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

            async def thread_messages(self, thread_id, limit=500):
                return []

            async def add_message(self, message):
                self.messages.append(message)

            async def put_entity(self, type_, data, **kwargs):
                self.entities.append((type_, dict(data)))

            async def get_entity(self, type_, entity_id):
                return None

        def make_task(action: str) -> dict:
            task = {
                "id": f"task_{action}",
                "title": f"Blocked {action}",
                "status": "blocked",
                "departmentId": "dept_a",
                "priority": "normal",
                "progress": 0.4,
                "createdAt": 100,
                "updatedAt": 100,
                "log": ["blocked ซ้ำจากระบบ"],
                "handoffs": [{"id": "ho_1", "fromDept": "dept_a", "toDept": "dept_b", "status": "requested"}],
            }
            if action == "restart_from_checkpoint":
                task["result"] = {"checkpointId": "chk_1"}
            return task

        for action in engine.EXECUTIVE_DECISION_ACTIONS:
            with self.subTest(action=action):
                repo = FakeRepo()
                task = make_task(action)

                request = await engine._create_executive_decision_request(
                    repo,
                    repo.departments["dept_a"],
                    task,
                    now=789,
                    reason=f"test action {action}",
                    trigger=engine.BLOCKED_RETRY_GUARD_REASON,
                    suggested_action=action,
                )

                self.assertEqual(request["selectedAction"], action)
                self.assertEqual(request["appliedAction"], action)
                self.assertEqual(request["appliedResult"]["action"], action)
                self.assertEqual(task["blockedRetryGuard"]["executiveAction"], action)
                self.assertTrue(any(kind == "executive_decision_request" for kind, _ in repo.entities))

                if action == "ask_clarification":
                    self.assertEqual(task["status"], "waiting")
                    self.assertEqual(task["waitingOn"]["reason"], engine.HANDOFF_CLARIFICATION_REASON)
                elif action == "request_file_again":
                    self.assertEqual(task["status"], "waiting")
                    self.assertEqual(task["waitingOn"]["reason"], engine.HANDOFF_MISSING_FILE_REASON)
                elif action == "reassign_task":
                    self.assertEqual(task["departmentId"], "dept_b")
                    self.assertEqual(task["status"], "assigned")
                    self.assertEqual(repo.departments["dept_b"]["currentTaskId"], task["id"])
                elif action == "split_task":
                    self.assertEqual(task["status"], "waiting")
                    self.assertTrue(task["subTaskIds"])
                    child = next(saved for saved in repo.saved_tasks if saved["id"] == task["subTaskIds"][0])
                    self.assertEqual(child["parentTaskId"], task["id"])
                elif action == "approve_assumption":
                    self.assertEqual(task["status"], "in_progress")
                    self.assertNotIn("waitingOn", task)
                    self.assertEqual(task["blockedRetryGuard"]["status"], "resolved")
                elif action == "restart_from_checkpoint":
                    self.assertEqual(task["status"], "in_progress")
                    self.assertEqual(task["result"]["restartFromCheckpoint"], "chk_1")
                elif action == "cancel_task":
                    self.assertEqual(task["status"], "cancelled")
                    self.assertEqual(task["blockedRetryGuard"]["status"], "resolved")
                elif action == "close_as_done":
                    self.assertEqual(task["status"], "done")
                    self.assertEqual(task["progress"], 1)
                    self.assertEqual(task["result"]["reviewStatus"], "closed_by_executive_auto_all")
                elif action == "manual_owner_input_required":
                    self.assertEqual(task["status"], "blocked")
                    self.assertEqual(task["waitingOn"]["reason"], engine.BLOCKED_RETRY_GUARD_REASON)

    async def test_handoff_missing_artifact_marks_missing_file_without_waking_target(self) -> None:
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

            async def get_entity(self, type_, entity_id):
                return None

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

        tmpdir = tempfile.TemporaryDirectory(prefix="atrium-handoff-missing-")
        self.addCleanup(tmpdir.cleanup)

        class FakeSettings:
            max_handoff_depth = 5
            max_consult_rounds = 3

            @property
            def workspace_dir(self):
                path = Path(tmpdir.name) / "workspace"
                path.mkdir(parents=True, exist_ok=True)
                return path

        repo = FakeRepo()
        dept = {"id": "source", "name": "Source", "agentName": "Source Agent", "state": "review", "currentTaskId": "task_1"}
        task = {
            "id": "task_1",
            "title": "Needs file",
            "detail": "Original task",
            "status": "review",
            "priority": "normal",
            "departmentId": "source",
            "progress": 0.5,
            "createdAt": 100,
            "updatedAt": 100,
            "handoffs": [],
            "log": [],
            "deliverables": ["art_missing"],
        }

        with mock.patch.object(engine, "get_settings", return_value=FakeSettings()):
            next_task = await engine._create_handoff_task(
                repo,
                dept,
                repo.departments["target"],
                task,
                reason="",
                kind="delegate",
                now=123,
            )

        self.assertIsNotNone(next_task)
        handoff = task["handoffs"][0]
        self.assertEqual(handoff["status"], "missing_file")
        self.assertEqual(task["waitingOn"]["reason"], engine.HANDOFF_MISSING_FILE_REASON)
        self.assertEqual(next_task["status"], "assigned")
        self.assertEqual(repo.departments["target"]["state"], "idle")
        packet_path = Path(handoff["contextPacketUri"])
        self.assertIn("`art_missing`: entity_missing", packet_path.read_text(encoding="utf-8"))

    async def test_return_handoff_auto_links_parent_and_unparks_source(self) -> None:
        from app import engine

        class FakeRepo:
            def __init__(self, original_task):
                self.original_task = original_task
                self.departments = {
                    "digital": {"id": "digital", "name": "Digital", "agentName": "Digital Agent", "state": "idle", "currentTaskId": None}
                }
                self.saved_tasks: list[dict] = []
                self.saved_departments: list[dict] = []
                self.activities: list[dict] = []
                self.messages: list[dict] = []
                self.entities: list[tuple[str, dict]] = []

            async def get_department(self, dept_id):
                return self.departments.get(dept_id)

            async def list_active_tasks(self, limit=None):
                return [self.original_task]

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

        tmpdir = tempfile.TemporaryDirectory(prefix="atrium-handoff-return-")
        self.addCleanup(tmpdir.cleanup)

        class FakeSettings:
            max_handoff_depth = 5
            max_consult_rounds = 3

            @property
            def workspace_dir(self):
                path = Path(tmpdir.name) / "workspace"
                path.mkdir(parents=True, exist_ok=True)
                return path

        parent = {
            "id": "ho_parent",
            "fromDept": "digital",
            "toDept": "creative",
            "ts": 100,
            "reason": "Need creative",
            "kind": "delegate",
            "status": "requested",
            "depth": 1,
            "chainId": "hc_1",
            "sourceTaskId": "task_digital",
            "targetTaskId": "task_creative",
            "messages": [],
        }
        original_task = {
            "id": "task_digital",
            "title": "Original research",
            "status": "waiting",
            "departmentId": "digital",
            "priority": "normal",
            "progress": 0.5,
            "createdAt": 90,
            "updatedAt": 100,
            "waitingOn": {"dept": "creative", "handoffId": "ho_parent", "reason": engine.HANDOFF_WAITING_REPLY_REASON},
            "handoffs": [dict(parent)],
            "log": [],
            "deliverables": [],
        }
        repo = FakeRepo(original_task)
        creative = {"id": "creative", "name": "Creative", "agentName": "Creative Agent", "state": "review", "currentTaskId": "task_creative"}
        creative_task = {
            "id": "task_creative",
            "title": "Creative continuation",
            "detail": "Creative work",
            "status": "review",
            "priority": "normal",
            "departmentId": "creative",
            "progress": 0.9,
            "createdAt": 100,
            "updatedAt": 200,
            "handoffs": [parent],
            "log": ["creative done"],
            "deliverables": [],
            "draftDeliverableMarkdown": "creative package",
        }

        with mock.patch.object(engine, "get_settings", return_value=FakeSettings()):
            next_task = await engine._create_handoff_task(
                repo,
                creative,
                repo.departments["digital"],
                creative_task,
                reason="return to digital",
                kind="return",
                now=300,
            )

        self.assertIsNotNone(next_task)
        child = creative_task["handoffs"][-1]
        self.assertEqual(child["kind"], "return")
        self.assertEqual(child["parentHandoffId"], "ho_parent")
        self.assertEqual(child["chainId"], "hc_1")
        self.assertEqual(creative_task["handoffs"][0]["status"], "returned")
        self.assertEqual(original_task["handoffs"][0]["status"], "closed")
        self.assertIsNone(original_task.get("waitingOn"))
        self.assertEqual(original_task["status"], "review")

    async def test_reconciler_clears_idle_current_waiting_task(self) -> None:
        from app import engine

        class FakeRepo:
            def __init__(self):
                self.tasks = [{
                    "id": "task_wait",
                    "title": "Waiting",
                    "status": "waiting",
                    "departmentId": "dept_a",
                    "createdAt": 1,
                    "updatedAt": 1,
                    "handoffs": [],
                    "log": [],
                }]
                self.departments = [{"id": "dept_a", "name": "Dept A", "state": "idle", "currentTaskId": "task_wait"}]
                self.saved_tasks: list[dict] = []
                self.saved_departments: list[dict] = []
                self.entities: list[tuple[str, dict]] = []

            async def list_active_tasks(self, limit=None):
                return self.tasks

            async def save_task(self, task):
                self.saved_tasks.append(dict(task))

            async def save_department(self, dept):
                self.saved_departments.append(dict(dept))

            async def put_entity(self, type_, data, **kwargs):
                self.entities.append((type_, dict(data)))

        repo = FakeRepo()
        changed = await engine._reconcile_handoff_workflow(repo, repo.departments, 999, force=True)

        self.assertEqual(changed, 1)
        self.assertIsNone(repo.departments[0]["currentTaskId"])
        self.assertTrue(any("ปลด currentTaskId" in line for line in repo.tasks[0]["log"]))

    async def test_report_work_status_tool_posts_routed_agent_summary(self) -> None:
        from app import chat_tools

        class FakeRepo:
            def __init__(self) -> None:
                self.departments = {
                    "source": {"id": "source", "name": "Source", "agentName": "Source Agent"},
                    "target": {"id": "target", "name": "Target", "agentName": "Target Agent"},
                    "exec": {"id": "exec", "name": "Executive", "agentName": "Exec"},
                }
                self.tasks = {
                    "task_1": {"id": "task_1", "title": "Tool visible task", "status": "in_progress", "progress": 0.4}
                }
                self.messages: list[dict] = []
                self.activities: list[dict] = []

            async def get_department(self, dept_id):
                return self.departments.get(dept_id)

            async def get_task(self, task_id):
                return self.tasks.get(task_id)

            async def add_message(self, message):
                self.messages.append(message)

            async def thread_messages(self, thread_id, limit=500):
                return [message for message in self.messages if message.get("threadId") == thread_id]

            async def add_activity(self, activity):
                self.activities.append(activity)

        repo = FakeRepo()
        result = await chat_tools._report_work_status_tool(
            repo,
            {
                "event": "handoff_delivered",
                "summary": "ส่งผลลัพธ์กลับให้ตรวจแล้ว",
                "taskId": "task_1",
                "handoffId": "ho_1",
                "targetDepartmentId": "target",
                "severity": "good",
            },
            repo.departments["source"],
            "dept:source",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["messageCount"], 3)
        self.assertEqual({message["threadId"] for message in repo.messages}, {"executive", "dept:source", "dept:target"})
        self.assertTrue(all(message["authorName"] == "Source Agent" for message in repo.messages))
        self.assertTrue(all((message.get("flow") or {}).get("kind") == "handoff" for message in repo.messages))

    def test_artifact_accepts_runtime_approval_tiers(self) -> None:
        from app.schema import Artifact, CreateHandoffMessageInput, Handoff, Task

        base = {
            "id": "art_1",
            "name": "Final",
            "kind": "report",
            "ownerDept": "exec",
            "version": 1,
            "status": "approved",
            "uri": "/tmp/final.md",
            "createdAt": 1,
            "createdBy": "exec",
            "updatedAt": 1,
            "updatedBy": "exec",
        }

        self.assertEqual(Artifact(**{**base, "approvalTier": "executive"}).approval_tier, "executive")
        self.assertEqual(Artifact(**{**base, "approvalTier": "full_auto"}).approval_tier, "full_auto")
        self.assertEqual(CreateHandoffMessageInput(**{"from": "creative", "act": "return", "text": "ส่งกลับ"}).act, "return")
        self.assertEqual(
            Handoff(
                id="ho_1",
                fromDept="a",
                toDept="b",
                ts=1,
                reason="handoff",
                kind="delegate",
                status="missing_file",
                chainId="hc_1",
                deliverableArtifactIds=["art_1"],
            ).status,
            "missing_file",
        )
        task = Task(
            id="task_1",
            title="Blocked",
            detail="details",
            status="waiting",
            priority="normal",
            departmentId="dept_a",
            origin={"kind": "executive"},
            progress=0.2,
            createdAt=1,
            updatedAt=2,
            blockedRetryCount=3,
            blockedRetryGuard={"status": "frozen"},
            lastUnblockAttemptAt=2,
            handoffChainId="hc_1",
            waitingOn={"dept": "executive", "reason": "blocked_retry_guard"},
        ).dump()
        self.assertEqual(task["blockedRetryCount"], 3)
        self.assertEqual(task["blockedRetryGuard"]["status"], "frozen")
        self.assertEqual(task["handoffChainId"], "hc_1")

    def test_job_stale_threshold_uses_job_timeout_not_heartbeat_only(self) -> None:
        from app.main import _job_stale_after_ms

        settings = SimpleNamespace(engine_stale_after_s=600.0, engine_job_timeout_s=1800.0)

        self.assertEqual(_job_stale_after_ms(settings), 1_800_000)

    def test_runtime_database_fingerprint_uses_effective_database_url(self) -> None:
        from app.main import _database_fingerprint

        settings = SimpleNamespace(
            database_url="",
            effective_database_url="sqlite+aiosqlite:////Users/mac/pp/ai-company/system/data/atrium.db",
        )

        fingerprint = _database_fingerprint(settings)

        self.assertEqual(fingerprint["backend"], "sqlite")
        self.assertTrue(fingerprint["configured"])
        self.assertFalse(fingerprint["explicitlyConfigured"])
        self.assertEqual(fingerprint["redacted"], "/Users/mac/pp/ai-company/system/data/atrium.db")
        self.assertTrue(fingerprint["fingerprint"])


if __name__ == "__main__":
    unittest.main()
