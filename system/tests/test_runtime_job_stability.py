import asyncio
from pathlib import Path
import tempfile
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
