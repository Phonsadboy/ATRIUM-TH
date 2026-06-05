import tempfile
import unittest
from unittest import mock


class FakeRepo:
    def __init__(self) -> None:
        self.departments = {}
        self.entities = []

    async def get_department(self, dept_id):
        dept = self.departments.get(dept_id)
        return dict(dept) if dept else None

    async def save_department(self, dept):
        self.departments[dept["id"]] = dict(dept)

    async def put_entity(self, kind, data, *, dept=None, status=None, ts=None):
        self.entities.append((kind, dict(data), dept, status, ts))


class SeedDefaultsTest(unittest.IsolatedAsyncioTestCase):
    async def test_first_boot_executive_defaults_to_claude_code_opus_48(self) -> None:
        from app.config import Settings
        from app import seed

        repo = FakeRepo()
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(data_dir=tmpdir)
            with mock.patch.object(seed, "get_settings", return_value=settings):
                changed = await seed.ensure_executive(repo)

        self.assertTrue(changed)
        executive = repo.departments["exec"]
        self.assertEqual(executive["providerId"], "claude_code")
        self.assertEqual(executive["model"], "claude-opus-4-8")
        self.assertTrue(executive["autonomy"])

    async def test_seed_blueprints_default_autonomy_to_executive_only(self) -> None:
        from app.config import Settings
        from app import seed

        now = 1_000_000
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(data_dir=tmpdir)
            with mock.patch.object(seed, "get_settings", return_value=settings):
                departments = [
                    seed._department_from_blueprint(bp, now=now, seed_demo_data=True)
                    for bp in seed.BLUEPRINTS
                ]

        autonomy_by_id = {dept["id"]: dept["autonomy"] for dept in departments}
        self.assertTrue(autonomy_by_id["exec"])
        self.assertTrue(all(not enabled for dept_id, enabled in autonomy_by_id.items() if dept_id != "exec"))

    async def test_checkpoint_restore_missing_autonomy_defaults_to_non_autonomous_department(self) -> None:
        from app.org import checkpoints

        restored = checkpoints._restore_department_spec({"id": "research", "name": "Research"})

        self.assertFalse(restored["autonomy"])

    async def test_checkpoint_restore_missing_autonomy_defaults_executive_to_autonomous(self) -> None:
        from app.org import checkpoints

        restored = checkpoints._restore_department_spec({"id": "exec", "name": "ผู้บริหาร"})

        self.assertTrue(restored["autonomy"])

    async def test_checkpoint_restore_preserves_explicit_disabled_autonomy(self) -> None:
        from app.org import checkpoints

        restored = checkpoints._restore_department_spec(
            {"id": "strategy", "name": "Strategy"},
            {"id": "strategy", "name": "Strategy", "autonomy": False},
        )

        self.assertFalse(restored["autonomy"])


if __name__ == "__main__":
    unittest.main()
