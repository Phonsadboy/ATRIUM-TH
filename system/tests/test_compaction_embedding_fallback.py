from types import SimpleNamespace
import json
import unittest
from unittest import mock


class BrokenEmbedder:
    name = "ollama:bge-m3"
    model = "bge-m3"
    dim = 1024

    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts):
        del texts
        self.calls += 1
        raise RuntimeError("ollama embed failed")


class FakeRepo:
    def __init__(self) -> None:
        self.archives: list[dict] = []
        self.knowledge: list[tuple[str, dict, list[float] | None, str | None, dict | None]] = []
        self.graph_nodes: list[tuple] = []
        self.graph_edges: list[tuple] = []
        self.saved_departments: list[dict] = []
        self.activities: list[dict] = []

    async def thread_messages(self, thread_id: str, *, limit: int = 500):
        del limit
        return [{"role": "user", "authorName": "Owner", "text": "remember this", "ts": 1, "threadId": thread_id}]

    async def add_archive(self, dept_id: str, entry: dict) -> None:
        self.archives.append({"deptId": dept_id, **entry})

    async def add_knowledge(self, dept_id: str, entry: dict, *, embedding=None, source=None, embedding_meta=None) -> None:
        self.knowledge.append((dept_id, dict(entry), embedding, source, dict(embedding_meta or {})))

    async def add_graph_node(self, *args, **kwargs) -> None:
        self.graph_nodes.append((args, kwargs))

    async def add_graph_edge(self, *args, **kwargs) -> None:
        self.graph_edges.append((args, kwargs))

    async def count_graph(self, dept_id: str):
        del dept_id
        return (len(self.graph_nodes), len(self.graph_edges))

    async def count_archive(self, dept_id: str):
        del dept_id
        return len(self.archives)

    async def list_knowledge(self, dept_id: str, limit: int = 50):
        del dept_id, limit
        return [entry for _, entry, *_ in self.knowledge]

    async def save_department(self, dept: dict) -> None:
        self.saved_departments.append(dict(dept))

    async def add_activity(self, activity: dict) -> None:
        self.activities.append(activity)


class CompactionEmbeddingFallbackTest(unittest.IsolatedAsyncioTestCase):
    async def test_compaction_falls_back_to_hash_embedding_when_primary_embed_fails(self) -> None:
        from app import engine
        from app.memory import ledger

        repo = FakeRepo()
        dept = {"id": "research", "name": "Research", "agentName": "Research AI", "role": "research", "memory": {}}
        broken = BrokenEmbedder()
        extraction = {
            "archiveSummary": "summary",
            "knowledge": [{"title": "Fact", "text": "Reusable fact", "tags": ["fact"], "score": 0.8}],
            "graphNodes": [],
            "graphEdges": [],
        }
        pulses: list[dict] = []

        async def fake_turn(*args, **kwargs):
            del args, kwargs
            return SimpleNamespace(text=json.dumps(extraction))

        with (
            mock.patch.object(engine, "_complete_engine_turn", new=fake_turn),
            mock.patch.object(engine, "_task_memory_context", new=mock.AsyncMock(return_value="")),
            mock.patch.object(engine, "resolve_embedder", new=mock.AsyncMock(return_value=broken)),
            mock.patch.object(engine.asyncio, "sleep", new=mock.AsyncMock()),
            mock.patch.object(ledger, "record_compaction_ledger", new=mock.AsyncMock()),
            mock.patch.object(engine.hub, "pulse", side_effect=lambda payload: pulses.append(payload)),
        ):
            ok = await engine._compact_department(repo, dept, now=1234, thread_id="thread:research")

        self.assertTrue(ok)
        self.assertEqual(broken.calls, 2)
        self.assertEqual(len(repo.archives), 1)
        fallback = repo.archives[0]["embeddingFallback"]
        self.assertEqual(fallback["status"], "fallback")
        self.assertEqual(fallback["primaryProvider"], "ollama:bge-m3")
        self.assertTrue(fallback["fallbackProvider"].startswith("hash-"))
        self.assertEqual(fallback["errorType"], "RuntimeError")
        self.assertEqual(len(repo.knowledge), 1)
        _, knowledge, embedding, source, meta = repo.knowledge[0]
        self.assertEqual(source, f"compact:{repo.archives[0]['id']}")
        self.assertIn("embedding:fallback", knowledge["tags"])
        self.assertIsInstance(embedding, list)
        self.assertTrue(meta["provider"].startswith("hash-"))
        self.assertTrue(any(activity.get("severity") == "warn" for activity in repo.activities))
        self.assertEqual(pulses[0]["embeddingFallback"]["status"], "fallback")


if __name__ == "__main__":
    unittest.main()
