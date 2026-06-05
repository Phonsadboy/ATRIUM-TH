import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CLI_PATH = ROOT / "ops" / "atrium_cli.py"
SPEC = importlib.util.spec_from_file_location("atrium_cli", CLI_PATH)
atrium_cli = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules["atrium_cli"] = atrium_cli
SPEC.loader.exec_module(atrium_cli)


class AtriumCliEnvTests(unittest.TestCase):
    def test_merge_env_preserves_existing_values_and_fills_empty_defaults(self) -> None:
        merged, changed, preserved = atrium_cli.merge_env_text(
            "ATRIUM_AGENT_BACKEND=engine\n"
            "ATRIUM_DATABASE_URL=\n"
            "ATRIUM_OPENAI_API_KEY=sk-secret\n",
            {
                "ATRIUM_AGENT_BACKEND": "native",
                "ATRIUM_DATABASE_URL": "postgresql+asyncpg://atrium:atrium@127.0.0.1:5432/atrium",
                "ATRIUM_GRAPH_BACKEND": "auto",
            },
        )

        self.assertIn("ATRIUM_AGENT_BACKEND=engine", merged)
        self.assertIn("ATRIUM_DATABASE_URL=postgresql+asyncpg://atrium:atrium@127.0.0.1:5432/atrium", merged)
        self.assertIn("ATRIUM_GRAPH_BACKEND=auto", merged)
        self.assertIn("ATRIUM_OPENAI_API_KEY=sk-secret", merged)
        self.assertEqual(changed, ["ATRIUM_DATABASE_URL", "ATRIUM_GRAPH_BACKEND"])
        self.assertEqual(preserved, ["ATRIUM_AGENT_BACKEND"])

    def test_update_env_file_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            update = atrium_cli.update_env_file(
                env_path,
                {"ATRIUM_AGENT_BACKEND": "native"},
                dry_run=True,
            )

            self.assertTrue(update.created)
            self.assertEqual(update.changed_keys, ["ATRIUM_AGENT_BACKEND"])
            self.assertFalse(env_path.exists())

    def test_redaction_hides_secret_values_and_database_passwords(self) -> None:
        text = atrium_cli.redact_text(
            "ATRIUM_OPENAI_API_KEY=sk-live-secret\n"
            "ATRIUM_DATABASE_URL=postgresql+asyncpg://atrium:atrium@127.0.0.1:5432/atrium\n"
            "ATRIUM_PORT=8787"
        )

        self.assertIn("ATRIUM_OPENAI_API_KEY=set", text)
        self.assertIn("ATRIUM_DATABASE_URL=postgresql+asyncpg://atrium:***@127.0.0.1:5432/atrium", text)
        self.assertIn("ATRIUM_PORT=8787", text)
        self.assertNotIn("sk-live-secret", text)


if __name__ == "__main__":
    unittest.main()
