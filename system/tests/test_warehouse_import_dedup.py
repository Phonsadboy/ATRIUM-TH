from types import SimpleNamespace
import unittest
from unittest import mock


class FakeEmbedder:
    provider_id = "fake"
    model = "fake-embed"
    dim = 3

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed(self, texts):
        self.calls.append(list(texts))
        return [[0.1, 0.2, 0.3] for _ in texts]


class FakeRepo:
    def __init__(self) -> None:
        self.entities: dict[tuple[str, str], dict] = {}
        self.puts: list[tuple[str, dict]] = []
        self.knowledge: list[tuple[str, dict, str | None]] = []
        self.refresh_calls: list[str] = []

    async def get_entity(self, etype: str, eid: str):
        return self.entities.get((etype, eid))

    async def list_entities(self, etype: str, *, dept=None, limit=500, **kwargs):
        del limit, kwargs
        return [
            item
            for (stored_type, _), item in self.entities.items()
            if stored_type == etype and (dept is None or item.get("departmentId") == dept)
        ]

    async def put_entity(self, etype: str, obj: dict, **kwargs):
        del kwargs
        self.entities[(etype, obj["id"])] = dict(obj)
        self.puts.append((etype, dict(obj)))
        return obj

    async def add_knowledge(self, dept_id: str, item: dict, *, source=None, **kwargs):
        del kwargs
        self.knowledge.append((dept_id, dict(item), source))

    async def refresh_department_memory_stats(self, department_id: str):
        self.refresh_calls.append(department_id)


class WarehouseImportDedupTest(unittest.IsolatedAsyncioTestCase):
    async def test_import_text_source_dedupes_same_source_and_text(self) -> None:
        from app.memory import warehouse

        repo = FakeRepo()
        embedder = FakeEmbedder()
        with (
            mock.patch.object(warehouse, "get_settings", return_value=SimpleNamespace()),
            mock.patch.object(warehouse, "resolve_embedder", new=mock.AsyncMock(return_value=embedder)),
        ):
            first = await warehouse.import_text_source(
                repo,
                department_id="research",
                title="Report",
                text="same text",
                source_uri="/tmp/report.txt",
                source_kind="file",
            )
            second = await warehouse.import_text_source(
                repo,
                department_id="research",
                title="Report again",
                text="same text",
                source_uri="/tmp/report.txt",
                source_kind="file",
            )

        self.assertEqual(first["id"], second["id"])
        self.assertTrue(second["deduped"])
        self.assertEqual(len(repo.puts), 1)
        self.assertEqual(len(repo.knowledge), 1)
        self.assertEqual(len(embedder.calls), 1)
        self.assertEqual(repo.refresh_calls, ["research"])

    async def test_import_text_source_dedupes_legacy_entry_without_dedupe_key(self) -> None:
        from app.memory import warehouse

        repo = FakeRepo()
        repo.entities[("knowledge_warehouse", "wh_legacy")] = {
            "id": "wh_legacy",
            "departmentId": "research",
            "title": "Legacy report",
            "text": "same text",
            "sourceUri": "/tmp/report.txt",
            "sourceKind": "file",
        }
        embedder = FakeEmbedder()
        with (
            mock.patch.object(warehouse, "get_settings", return_value=SimpleNamespace()),
            mock.patch.object(warehouse, "resolve_embedder", new=mock.AsyncMock(return_value=embedder)),
        ):
            result = await warehouse.import_text_source(
                repo,
                department_id="research",
                title="Report",
                text="same text",
                source_uri="/tmp/report.txt",
                source_kind="file",
            )

        self.assertEqual(result["id"], "wh_legacy")
        self.assertTrue(result["deduped"])
        self.assertEqual(result["duplicateOf"], "wh_legacy")
        self.assertEqual(repo.puts, [])
        self.assertEqual(repo.knowledge, [])
        self.assertEqual(embedder.calls, [])

    async def test_import_text_source_allows_changed_text_from_same_source(self) -> None:
        from app.memory import warehouse

        repo = FakeRepo()
        embedder = FakeEmbedder()
        with (
            mock.patch.object(warehouse, "get_settings", return_value=SimpleNamespace()),
            mock.patch.object(warehouse, "resolve_embedder", new=mock.AsyncMock(return_value=embedder)),
        ):
            first = await warehouse.import_text_source(
                repo,
                department_id="research",
                title="Report",
                text="old text",
                source_uri="/tmp/report.txt",
                source_kind="file",
            )
            second = await warehouse.import_text_source(
                repo,
                department_id="research",
                title="Report",
                text="new text",
                source_uri="/tmp/report.txt",
                source_kind="file",
            )

        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(len(repo.puts), 2)
        self.assertEqual(len(repo.knowledge), 2)
        self.assertEqual(len(embedder.calls), 2)


if __name__ == "__main__":
    unittest.main()
