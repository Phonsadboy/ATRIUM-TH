from contextlib import asynccontextmanager
from types import SimpleNamespace
import unittest
from unittest import mock


class FakeSession:
    def __init__(self, row) -> None:
        self.row = row

    async def get(self, _model, _id):
        return self.row


class FakeScalarResult:
    def __init__(self, row) -> None:
        self.row = row

    def first(self):
        return self.row


class FakeExecuteResult:
    def __init__(self, row) -> None:
        self.row = row

    def scalars(self):
        return FakeScalarResult(self.row)


class FakeGraphSession:
    def __init__(self, existing=None) -> None:
        self.existing = existing
        self.added: list[object] = []
        self.execute_calls = 0

    async def execute(self, _query):
        self.execute_calls += 1
        return FakeExecuteResult(self.existing)

    def add(self, row) -> None:
        self.added.append(row)


class KnowledgeEditEmbeddingTest(unittest.IsolatedAsyncioTestCase):
    async def test_edit_knowledge_text_clears_stale_embedding(self) -> None:
        from app.db.repo import Repo

        row = SimpleNamespace(
            id="kn_1",
            department_id="research",
            title="เดิม",
            text="ข้อความเดิม",
            tags=["old"],
            ts=1,
            embedding=[0.1, 0.2],
            embedding_provider="hash",
            embedding_model="hash-256",
            embedding_dim=256,
            embedding_ts=10,
        )
        repo = Repo(FakeSession(row))
        repo._set_pgvector_embedding = mock.AsyncMock()

        ok = await repo.edit_knowledge("research", "kn_1", {"text": "ข้อความใหม่"})

        self.assertTrue(ok)
        self.assertEqual(row.text, "ข้อความใหม่")
        self.assertIsNone(row.embedding)
        self.assertIsNone(row.embedding_provider)
        self.assertIsNone(row.embedding_model)
        self.assertIsNone(row.embedding_dim)
        self.assertIsNone(row.embedding_ts)
        repo._set_pgvector_embedding.assert_awaited_once_with("kn_1", None)

    async def test_edit_knowledge_title_only_keeps_embedding(self) -> None:
        from app.db.repo import Repo

        row = SimpleNamespace(
            id="kn_1",
            department_id="research",
            title="เดิม",
            text="ข้อความเดิม",
            tags=["old"],
            ts=1,
            embedding=[0.1, 0.2],
            embedding_provider="hash",
            embedding_model="hash-256",
            embedding_dim=256,
            embedding_ts=10,
        )
        repo = Repo(FakeSession(row))
        repo._set_pgvector_embedding = mock.AsyncMock()

        ok = await repo.edit_knowledge("research", "kn_1", {"title": "หัวข้อใหม่"})

        self.assertTrue(ok)
        self.assertEqual(row.title, "หัวข้อใหม่")
        self.assertEqual(row.embedding, [0.1, 0.2])
        self.assertEqual(row.embedding_provider, "hash")
        repo._set_pgvector_embedding.assert_not_awaited()


class KnowledgeEmbeddingRouteTest(unittest.IsolatedAsyncioTestCase):
    async def test_embedding_migration_routes_call_repo_methods(self) -> None:
        from app import main

        class MigrationRepo:
            def __init__(self, _session):
                self.status_calls: list[int] = []
                self.reembed_calls: list[tuple[int, int]] = []

            async def knowledge_embedding_migration_status(self, *, limit=20):
                self.status_calls.append(limit)
                return {"ok": True, "limit": limit}

            async def reembed_stale_knowledge(self, *, limit=1000, batch_size=8):
                self.reembed_calls.append((limit, batch_size))
                return {"updated": 2, "limit": limit, "batchSize": batch_size}

        repo = MigrationRepo(object())

        @asynccontextmanager
        async def fake_session_scope():
            yield object()

        with (
            mock.patch.object(main, "session_scope", fake_session_scope),
            mock.patch.object(main, "Repo", lambda _session: repo),
            mock.patch.object(main.hub, "mark_dirty"),
        ):
            status = await main.knowledge_embedding_migration_status(limit=7)
            result = await main.reembed_stale_knowledge(limit=11, batch_size=3)

        self.assertEqual(status, {"ok": True, "limit": 7})
        self.assertEqual(result, {"updated": 2, "limit": 11, "batchSize": 3})
        self.assertEqual(repo.status_calls, [7])
        self.assertEqual(repo.reembed_calls, [(11, 3)])


class GraphEdgeDedupTest(unittest.IsolatedAsyncioTestCase):
    async def test_add_graph_edge_updates_existing_edge_instead_of_inserting_duplicate(self) -> None:
        from app.db.repo import Repo

        existing = SimpleNamespace(
            department_id="research",
            from_id="research:source",
            to_id="research:target",
            rel="relates_to",
            valid_from=100,
            valid_to=None,
            confidence=0.4,
            source="first",
        )
        session = FakeGraphSession(existing)
        repo = Repo(session)

        await repo.add_graph_edge(
            "research",
            "source",
            "target",
            "relates_to",
            valid_from=200,
            valid_to=300,
            confidence=0.9,
            source="second",
        )

        self.assertEqual(session.execute_calls, 1)
        self.assertEqual(session.added, [])
        self.assertEqual(existing.valid_from, 100)
        self.assertEqual(existing.valid_to, 300)
        self.assertEqual(existing.confidence, 0.9)
        self.assertEqual(existing.source, "second")

    async def test_add_graph_edge_inserts_when_edge_is_new(self) -> None:
        from app.db.repo import Repo

        session = FakeGraphSession(existing=None)
        repo = Repo(session)

        with mock.patch("app.db.repo.get_graph_mirror") as graph_mirror:
            graph_mirror.return_value.add_edge = mock.Mock()
            await repo.add_graph_edge("research", "source", "target", "relates_to", confidence=0.2, source="new")

        self.assertEqual(len(session.added), 1)
        row = session.added[0]
        self.assertEqual(row.department_id, "research")
        self.assertEqual(row.from_id, "research:source")
        self.assertEqual(row.to_id, "research:target")
        self.assertEqual(row.rel, "relates_to")
        self.assertEqual(row.confidence, 0.2)
        self.assertEqual(row.source, "new")
        graph_mirror.return_value.add_edge.assert_called_once_with("research", "research:source", "research:target", "relates_to")


if __name__ == "__main__":
    unittest.main()
