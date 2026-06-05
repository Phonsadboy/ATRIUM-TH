import unittest
from unittest import mock


class StateSnapshotDefaultsTest(unittest.TestCase):
    def test_default_snapshot_includes_task_draft_deliverable(self) -> None:
        from app.config import Settings
        from app.db import repo as repo_module

        settings = Settings()
        self.assertEqual(settings.state_task_detail_char_limit, 320)
        self.assertEqual(settings.state_task_deliverable_char_limit, 12_000)

        task = {
            "id": "task_1",
            "detail": "detail",
            "log": [],
            "handoffs": [],
            "draftDeliverableMarkdown": "draft deliverable",
        }

        compact = repo_module._compact_task_for_snapshot(task)

        self.assertEqual(compact["draftDeliverableMarkdown"], "draft deliverable")

    def test_snapshot_draft_deliverable_still_clips_to_limit(self) -> None:
        from app.config import Settings
        from app.db import repo as repo_module

        task = {
            "id": "task_1",
            "detail": "detail",
            "log": [],
            "handoffs": [],
            "draftDeliverableMarkdown": "x" * 40,
        }

        with mock.patch.object(repo_module, "get_settings", return_value=Settings(state_task_deliverable_chars=10)):
            compact = repo_module._compact_task_for_snapshot(task)

        self.assertEqual(compact["draftDeliverableMarkdown"], "xxxxxxx...")

    def test_snapshot_handoff_keeps_recent_message_preview(self) -> None:
        from app.config import Settings
        from app.db import repo as repo_module

        task = {
            "id": "task_1",
            "detail": "detail",
            "log": [],
            "handoffs": [
                {
                    "id": "handoff_1",
                    "fromDept": "creative",
                    "toDept": "engineering",
                    "ts": 1,
                    "reason": "needs implementation",
                    "kind": "delegate",
                    "status": "requested",
                    "messages": [
                        {"id": "msg_1", "handoffId": "handoff_1", "from": "creative", "act": "request", "text": "old", "ts": 1},
                        {"id": "msg_2", "handoffId": "handoff_1", "from": "engineering", "act": "reply", "text": "middle", "ts": 2},
                        {
                            "id": "msg_3",
                            "handoffId": "handoff_1",
                            "from": "creative",
                            "act": "deliver",
                            "text": "x" * 80,
                            "ts": 3,
                        },
                    ],
                }
            ],
            "draftDeliverableMarkdown": "",
        }

        settings = Settings(state_task_handoff_messages=2, state_task_handoff_message_chars=40)
        with mock.patch.object(repo_module, "get_settings", return_value=settings):
            compact = repo_module._compact_task_for_snapshot(task)

        handoff = compact["handoffs"][0]
        self.assertEqual(handoff["messageCount"], 3)
        self.assertTrue(handoff["messagesTruncated"])
        self.assertEqual([message["id"] for message in handoff["messages"]], ["msg_2", "msg_3"])
        self.assertEqual(handoff["messages"][0]["text"], "middle")
        self.assertEqual(len(handoff["messages"][1]["text"]), 40)
        self.assertTrue(handoff["messages"][1]["textTruncated"])


if __name__ == "__main__":
    unittest.main()
