import unittest
from unittest import mock


class FakeSession:
    def __init__(self) -> None:
        self.info = {}


class FakeHub:
    def __init__(self) -> None:
        self.activities = []
        self.dirty = 0

    def activity(self, event):
        self.activities.append(event)

    def mark_dirty(self):
        self.dirty += 1


class ActivityRealtimeTest(unittest.TestCase):
    def test_publish_session_activity_broadcasts_and_clears_committed_events(self) -> None:
        from app import events
        from app.db import base

        session = FakeSession()
        event = {
            "id": "act_1",
            "ts": 1_000,
            "type": "system",
            "departmentId": "research",
            "text": "Research started",
            "severity": "info",
        }
        hub = FakeHub()

        base.record_session_activity(session, event)
        with mock.patch.object(events, "hub", hub):
            base.publish_session_activity(session)
            base.publish_session_activity(session)

        self.assertEqual(hub.activities, [event])
        self.assertEqual(hub.dirty, 1)
        self.assertEqual(session.info, {})


if __name__ == "__main__":
    unittest.main()
