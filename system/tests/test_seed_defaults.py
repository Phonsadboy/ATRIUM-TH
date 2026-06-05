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


if __name__ == "__main__":
    unittest.main()
