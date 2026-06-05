import asyncio
import unittest


class CadenceIntervalTest(unittest.TestCase):
    def test_cron_steps_use_their_field_units(self) -> None:
        from app.scheduling import DAY_MS, HOUR_MS, MINUTE_MS, cadence_interval_ms

        self.assertEqual(cadence_interval_ms("*/5 * * * *"), 5 * MINUTE_MS)
        self.assertEqual(cadence_interval_ms("0 */2 * * *"), 2 * HOUR_MS)
        self.assertEqual(cadence_interval_ms("0 0 */3 * *"), 3 * DAY_MS)

    def test_non_cron_step_shorthand_keeps_minute_fallback(self) -> None:
        from app.scheduling import MINUTE_MS, cadence_interval_ms

        self.assertEqual(cadence_interval_ms("*/7"), 7 * MINUTE_MS)

    def test_one_shot_zero_is_explicit_schedule_value(self) -> None:
        from app.scheduling import has_one_shot_at, next_run_for_cadence

        self.assertTrue(has_one_shot_at(0))
        self.assertEqual(next_run_for_cadence(None, 0), 0)


class ObjectiveEnqueueLockTest(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_objective_enqueue_dedupes_with_lock(self) -> None:
        from app import engine

        class Repo:
            def __init__(self) -> None:
                self.objectives = [{
                    "id": "obj_1",
                    "title": "Daily check",
                    "departmentId": "research",
                    "cadence": "every day",
                    "enabled": True,
                    "nextRunAt": 1_000,
                }]
                self.jobs: list[dict] = []

            async def list_objectives(self):
                await asyncio.sleep(0)
                return [dict(obj) for obj in self.objectives]

            async def enqueue(self, job_id, kind, payload, now, *, priority=5):
                await asyncio.sleep(0)
                self.jobs.append({"id": job_id, "kind": kind, "payload": payload, "now": now, "priority": priority})

            async def save_objective(self, obj):
                await asyncio.sleep(0)
                self.objectives = [dict(obj)]

        repo = Repo()

        counts = await asyncio.gather(
            engine._enqueue_due_objectives(repo, 2_000),
            engine._enqueue_due_objectives(repo, 2_000),
        )

        self.assertEqual(sum(counts), 1)
        self.assertEqual(len(repo.jobs), 1)
        self.assertEqual(repo.jobs[0]["payload"]["objectiveId"], "obj_1")
        self.assertGreater(repo.objectives[0]["nextRunAt"], 2_000)

    async def test_objective_catch_up_jobs_are_staggered_without_dropping_runs(self) -> None:
        from app import engine

        class Repo:
            def __init__(self) -> None:
                self.objective = {
                    "id": "obj_1",
                    "title": "Frequent check",
                    "departmentId": "research",
                    "cadence": "*/1",
                    "enabled": True,
                    "nextRunAt": 1_000,
                }
                self.jobs: list[dict] = []

            async def list_objectives(self):
                return [dict(self.objective)]

            async def enqueue(self, job_id, kind, payload, run_after, *, priority=5):
                self.jobs.append({
                    "id": job_id,
                    "kind": kind,
                    "payload": payload,
                    "runAfter": run_after,
                    "priority": priority,
                })

            async def save_objective(self, obj):
                self.objective = dict(obj)

        repo = Repo()

        count = await engine._enqueue_due_objectives(repo, 10 * 60_000)

        self.assertEqual(count, engine.MAX_TRIGGER_CATCH_UP_RUNS)
        self.assertEqual(len(repo.jobs), engine.MAX_TRIGGER_CATCH_UP_RUNS)
        self.assertEqual(
            [job["runAfter"] for job in repo.jobs],
            [10 * 60_000 + i * engine.CATCH_UP_RUN_SPACING_MS for i in range(engine.MAX_TRIGGER_CATCH_UP_RUNS)],
        )
        self.assertTrue(all(job["payload"]["catchUp"] for job in repo.jobs))
        self.assertEqual(repo.objective["lastRunAt"], 421_000)
        self.assertEqual(repo.objective["nextRunAt"], 481_000)

    async def test_trigger_one_shot_zero_is_not_treated_as_recurring(self) -> None:
        from app import engine

        test_case = self

        class Repo:
            def __init__(self) -> None:
                self.trigger = {
                    "id": "trig_1",
                    "title": "Immediate one-shot",
                    "kind": "cron",
                    "target": "executive",
                    "enabled": True,
                    "oneShotAt": 0,
                    "nextRunAt": 0,
                }
                self.jobs: list[dict] = []

            async def list_entities(self, etype: str, *, limit: int = 1000):
                test_case.assertEqual(etype, "trigger")
                del limit
                return [dict(self.trigger)]

            async def enqueue(self, job_id, kind, payload, now, *, priority=5):
                self.jobs.append({"id": job_id, "kind": kind, "payload": payload, "now": now, "priority": priority})

            async def put_entity(self, etype: str, obj: dict, **kwargs):
                test_case.assertEqual(etype, "trigger")
                del kwargs
                self.trigger = dict(obj)

        repo = Repo()

        count = await engine._enqueue_due_triggers(repo, 0)

        self.assertEqual(count, 1)
        self.assertEqual(len(repo.jobs), 1)
        self.assertFalse(repo.trigger["enabled"])
        self.assertIsNone(repo.trigger["nextRunAt"])

    async def test_trigger_catch_up_jobs_are_staggered_without_dropping_runs(self) -> None:
        from app import engine

        test_case = self

        class Repo:
            def __init__(self) -> None:
                self.trigger = {
                    "id": "trig_1",
                    "title": "Frequent trigger",
                    "kind": "cron",
                    "target": "executive",
                    "enabled": True,
                    "cadence": "*/1",
                    "nextRunAt": 1_000,
                }
                self.jobs: list[dict] = []

            async def list_entities(self, etype: str, *, limit: int = 1000):
                test_case.assertEqual(etype, "trigger")
                del limit
                return [dict(self.trigger)]

            async def enqueue(self, job_id, kind, payload, run_after, *, priority=5):
                self.jobs.append({
                    "id": job_id,
                    "kind": kind,
                    "payload": payload,
                    "runAfter": run_after,
                    "priority": priority,
                })

            async def put_entity(self, etype: str, obj: dict, **kwargs):
                test_case.assertEqual(etype, "trigger")
                del kwargs
                self.trigger = dict(obj)

        repo = Repo()

        count = await engine._enqueue_due_triggers(repo, 10 * 60_000)

        self.assertEqual(count, engine.MAX_TRIGGER_CATCH_UP_RUNS)
        self.assertEqual(len(repo.jobs), engine.MAX_TRIGGER_CATCH_UP_RUNS)
        self.assertEqual(
            [job["runAfter"] for job in repo.jobs],
            [10 * 60_000 + i * engine.CATCH_UP_RUN_SPACING_MS for i in range(engine.MAX_TRIGGER_CATCH_UP_RUNS)],
        )
        self.assertTrue(all(job["payload"]["catchUp"] for job in repo.jobs))
        self.assertEqual(repo.trigger["lastRunAt"], 421_000)
        self.assertEqual(repo.trigger["nextRunAt"], 481_000)


if __name__ == "__main__":
    unittest.main()
