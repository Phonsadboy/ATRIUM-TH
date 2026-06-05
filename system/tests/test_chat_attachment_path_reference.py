import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import unittest

from app import main
from app.chat_input import attachment_context
from app.file_intake import message_attachment_from_artifact
from app.schema import AttachmentReferenceInput


class FakeRepo:
    def __init__(self) -> None:
        self.entities: dict[tuple[str, str], dict] = {}
        self.knowledge: list[tuple[str, dict, str | None]] = []
        self.activities: list[dict] = []

    async def put_entity(self, kind: str, payload: dict, **_kwargs) -> None:
        self.entities[(kind, payload["id"])] = payload

    async def get_entity(self, kind: str, entity_id: str) -> dict | None:
        return self.entities.get((kind, entity_id))

    async def get_department(self, department_id: str) -> dict | None:
        return {"id": department_id, "name": department_id}

    async def add_knowledge(self, dept: str, knowledge: dict, *, source: str | None = None) -> None:
        self.knowledge.append((dept, knowledge, source))

    async def add_activity(self, activity: dict) -> None:
        self.activities.append(activity)


class FakeSessionScope:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_exc) -> None:
        return None


class ChatAttachmentPathReferenceTest(unittest.IsolatedAsyncioTestCase):
    async def test_path_reference_artifact_keeps_source_path_without_full_copy(self) -> None:
        repo = FakeRepo()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "report.txt"
            source.write_text("hello from local path\n" * 3, encoding="utf-8")
            workspace = root / "workspace"

            with (
                mock.patch.object(main, "get_settings", return_value=SimpleNamespace(object_store_enabled=False)),
                mock.patch.object(main, "_workspace_for_dept", return_value=workspace),
            ):
                result = await main._store_path_reference_artifact(
                    repo,
                    source=source,
                    owner_dept="engineering",
                    artifact_name="Local report",
                )

            artifact = result["artifact"]
            self.assertEqual(artifact["uri"], str(source.resolve()))
            self.assertEqual(artifact["storage"], "external")
            self.assertEqual(artifact["contentStatus"], "referenced")
            self.assertEqual(artifact["copyStatus"], "not_copied")
            self.assertEqual(artifact["referenceKind"], "local_path")
            self.assertEqual(artifact["sourcePath"], str(source.resolve()))
            self.assertIn("path_reference", artifact["tags"])
            self.assertEqual(artifact["links"], [str(source.resolve())])
            self.assertTrue((workspace / "imports" / f"{artifact['id']}.preview.md").exists())
            self.assertFalse((workspace / "imports" / f"{artifact['id']}.txt").exists())

            attachment = message_attachment_from_artifact(artifact)
            self.assertEqual(attachment["sourcePath"], str(source.resolve()))
            self.assertEqual(attachment["copyStatus"], "not_copied")

    async def test_attachment_context_marks_truncated_path_reference_content(self) -> None:
        repo = FakeRepo()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "long.txt"
            source.write_text("a" * 12_000, encoding="utf-8")
            with (
                mock.patch.object(main, "get_settings", return_value=SimpleNamespace(object_store_enabled=False)),
                mock.patch.object(main, "_workspace_for_dept", return_value=root / "workspace"),
            ):
                result = await main._store_path_reference_artifact(repo, source=source, owner_dept="engineering")

            artifact = result["artifact"]
            context = await attachment_context(repo, [message_attachment_from_artifact(artifact)])

        self.assertIn("Attachment", context)
        self.assertIn("attachment context truncated at 10000 chars", context)
        self.assertIn("use the artifact id, uri, or sourcePath", context)

    async def test_attachment_context_respects_context_max_chars_override(self) -> None:
        repo = FakeRepo()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "long.txt"
            source.write_text("b" * 12_000, encoding="utf-8")
            with (
                mock.patch.object(main, "get_settings", return_value=SimpleNamespace(object_store_enabled=False)),
                mock.patch.object(main, "_workspace_for_dept", return_value=root / "workspace"),
            ):
                result = await main._store_path_reference_artifact(repo, source=source, owner_dept="engineering")

            attachment = message_attachment_from_artifact(result["artifact"])
            attachment["contextMaxChars"] = 12_050
            context = await attachment_context(repo, [attachment])

        self.assertIn("b" * 12_000, context)
        self.assertNotIn("attachment context truncated", context)

    async def test_attachment_context_include_full_context_uses_known_source_size(self) -> None:
        repo = FakeRepo()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "long.txt"
            source.write_text("c" * 12_000, encoding="utf-8")
            with (
                mock.patch.object(main, "get_settings", return_value=SimpleNamespace(object_store_enabled=False)),
                mock.patch.object(main, "_workspace_for_dept", return_value=root / "workspace"),
            ):
                result = await main._store_path_reference_artifact(repo, source=source, owner_dept="engineering")

            attachment = message_attachment_from_artifact(result["artifact"])
            attachment["includeFullContext"] = True
            context = await attachment_context(repo, [attachment])

        self.assertIn("c" * 12_000, context)
        self.assertNotIn("attachment context truncated", context)

    async def test_normalize_chat_attachments_preserves_context_override_for_artifact(self) -> None:
        repo = FakeRepo()
        repo.entities[("artifact", "art_1")] = {
            "id": "art_1",
            "name": "report.txt",
            "kind": "report",
            "mime": "text/plain",
            "uri": "/tmp/report.txt",
            "contentSizeBytes": 123,
        }
        input_payload = SimpleNamespace(
            attachment_ids=[],
            attachments=[{"artifactId": "art_1", "contextMaxChars": 24_000, "includeFullContext": True}],
        )

        attachments = await main._normalize_chat_attachments(repo, input_payload)

        self.assertEqual(attachments[0]["artifactId"], "art_1")
        self.assertEqual(attachments[0]["contextMaxChars"], 24_000)
        self.assertTrue(attachments[0]["includeFullContext"])

    async def test_open_local_file_tool_respects_max_chars_for_artifact_preview(self) -> None:
        from app import chat_tools

        repo = FakeRepo()
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "big.txt"
            source.write_text("d" * 22_000, encoding="utf-8")
            repo.entities[("artifact", "art_big")] = {
                "id": "art_big",
                "name": "big.txt",
                "kind": "report",
                "mime": "text/plain",
                "contentMime": "text/plain",
                "uri": str(source),
                "contentSizeBytes": 22_000,
                "sourceSizeBytes": 22_000,
            }

            result = await chat_tools._open_local_file_tool(
                repo,
                {"artifactId": "art_big", "maxChars": 21_000},
                {"id": "engineering", "name": "engineering", "agentName": "Engineering AI"},
            )

        self.assertEqual(result["preview"]["maxChars"], 21_000)
        self.assertEqual(len(result["preview"]["text"]), 21_000)
        self.assertEqual(result["preview"]["text"], "d" * 21_000)

    async def test_reference_attachment_defaults_to_path_reference(self) -> None:
        repo = FakeRepo()
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "report.txt"
            source.write_text("hello", encoding="utf-8")
            path_result = {"artifact": {"id": "art_path", "name": "report.txt", "kind": "report", "ownerDept": "exec", "version": 1, "status": "approved", "uri": str(source), "tags": [], "links": [], "createdAt": 1, "createdBy": "owner-ui", "updatedAt": 1, "updatedBy": "owner-ui"}, "knowledge": None}

            async def fake_path_store(_repo, **kwargs):
                self.assertEqual(kwargs["source"], source.resolve())
                return path_result

            with (
                mock.patch.object(main, "session_scope", return_value=FakeSessionScope()),
                mock.patch.object(main, "Repo", return_value=repo),
                mock.patch.object(main, "_store_path_reference_artifact", new=fake_path_store) as path_store,
                mock.patch.object(main, "_store_file_artifact", new=mock.AsyncMock()) as file_store,
                mock.patch.object(main.hub, "mark_dirty"),
            ):
                result = await main.reference_attachment(AttachmentReferenceInput(sourcePath=str(source), threadId="thread_exec"))

        self.assertEqual(result["artifact"]["id"], "art_path")
        self.assertIsNotNone(path_store)
        file_store.assert_not_awaited()

    async def test_reference_attachment_can_copy_to_workspace_when_requested(self) -> None:
        repo = FakeRepo()
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "report.txt"
            source.write_text("copy me", encoding="utf-8")
            seen: dict[str, object] = {}
            copied_result = {"artifact": {"id": "art_copy", "name": "report.txt", "kind": "report", "ownerDept": "exec", "version": 1, "status": "approved", "uri": "/workspace/report.txt", "tags": [], "links": [], "createdAt": 1, "createdBy": "owner-ui", "updatedAt": 1, "updatedBy": "owner-ui"}, "knowledge": None}

            async def fake_file_store(_repo, **kwargs):
                seen.update(kwargs)
                return copied_result

            with (
                mock.patch.object(main, "session_scope", return_value=FakeSessionScope()),
                mock.patch.object(main, "Repo", return_value=repo),
                mock.patch.object(main, "_store_path_reference_artifact", new=mock.AsyncMock()) as path_store,
                mock.patch.object(main, "_store_file_artifact", new=fake_file_store),
                mock.patch.object(main.hub, "mark_dirty"),
            ):
                result = await main.reference_attachment(
                    AttachmentReferenceInput(sourcePath=str(source), threadId="thread_exec", copyToWorkspace=True)
                )

        self.assertEqual(result["artifact"]["id"], "art_copy")
        self.assertEqual(seen["data"], b"copy me")
        self.assertEqual(seen["links"], [str(source.resolve())])
        path_store.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
