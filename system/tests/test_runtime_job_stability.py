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

    def test_job_stale_threshold_uses_job_timeout_not_heartbeat_only(self) -> None:
        from app.main import _job_stale_after_ms

        settings = SimpleNamespace(engine_stale_after_s=600.0, engine_job_timeout_s=1800.0)

        self.assertEqual(_job_stale_after_ms(settings), 1_800_000)


if __name__ == "__main__":
    unittest.main()
