import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db import tables as T
from app.db.repo import Repo


def _message(i: int) -> dict:
    ts = 1000 + (i // 5)
    msg_id = f"msg_{i:03d}"
    return {
        "id": msg_id,
        "threadId": "executive",
        "role": "system",
        "authorName": "system",
        "text": f"message {i}",
        "ts": ts,
    }


class ThreadMessagePaginationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _seed_messages(self, count: int = 75) -> None:
        async with self.sessionmaker() as session:
            for i in range(count):
                msg = _message(i)
                session.add(T.Message(id=msg["id"], thread_id=msg["threadId"], ts=msg["ts"], data=msg))
            await session.commit()

    async def _seed_thread_messages(self) -> None:
        async with self.sessionmaker() as session:
            for thread_index, (thread_id, base_ts) in enumerate(
                [
                    ("old_preferred", 100),
                    ("newest", 500),
                    ("recent_b", 400),
                    ("recent_c", 300),
                    ("recent_d", 200),
                ]
            ):
                for i in range(3):
                    msg_id = f"{thread_id}_{i}"
                    msg = {
                        "id": msg_id,
                        "threadId": thread_id,
                        "role": "system",
                        "authorName": "system",
                        "text": f"thread {thread_index} message {i}",
                        "ts": base_ts + i,
                    }
                    session.add(T.Message(id=msg_id, thread_id=thread_id, ts=msg["ts"], data=msg))
            await session.commit()

    async def test_before_cursor_pages_without_gaps_when_timestamps_match(self) -> None:
        await self._seed_messages()
        async with self.sessionmaker() as session:
            repo = Repo(session)
            newest = await repo.thread_messages("executive", limit=30)
            older = await repo.thread_messages_before(
                "executive",
                before_ts=newest[0]["ts"],
                before_id=newest[0]["id"],
                limit=30,
            )
            oldest = await repo.thread_messages_before(
                "executive",
                before_ts=older[0]["ts"],
                before_id=older[0]["id"],
                limit=30,
            )

        combined = [*oldest, *older, *newest]
        self.assertEqual([m["id"] for m in combined], [f"msg_{i:03d}" for i in range(75)])

    async def test_after_cursor_pages_without_duplicates_when_timestamps_match(self) -> None:
        await self._seed_messages()
        async with self.sessionmaker() as session:
            repo = Repo(session)
            first = await repo.thread_messages_after("executive", after_ts=1000, after_id="msg_004", limit=30)
            second = await repo.thread_messages_after(
                "executive",
                after_ts=first[-1]["ts"],
                after_id=first[-1]["id"],
                limit=30,
            )

        combined = [*first, *second]
        self.assertEqual([m["id"] for m in combined], [f"msg_{i:03d}" for i in range(5, 65)])

    async def test_reconciled_chat_reply_moves_to_completion_time_for_after_cursor(self) -> None:
        async with self.sessionmaker() as session:
            pending = {
                "id": "reply_1",
                "threadId": "executive",
                "role": "executive",
                "authorName": "ออตโต้",
                "text": "กำลังคิดและทำงานต่อในคิวเบื้องหลัง...",
                "ts": 1000,
                "pending": True,
                "status": "sending",
                "replyToMessageId": "user_1",
            }
            session.add(T.Message(id="reply_1", thread_id="executive", ts=1000, data=pending))
            session.add(T.Job(
                id="job_1",
                kind="chat_reply",
                status="failed",
                run_after=1000,
                priority=1,
                payload={"replyMessageId": "reply_1"},
                attempts=1,
                last_error="provider stopped",
                created_at=1000,
                updated_at=5000,
            ))
            await session.commit()

        async with self.sessionmaker() as session:
            repo = Repo(session)
            repaired = await repo.reconcile_chat_reply_placeholders()
            newer = await repo.thread_messages_after("executive", after_ts=3000, after_id="later", limit=30)

        self.assertEqual(repaired, 1)
        self.assertEqual([m["id"] for m in newer], ["reply_1"])
        self.assertEqual(newer[0]["ts"], 5000)
        self.assertEqual(newer[0]["completedAt"], 5000)
        self.assertFalse(newer[0]["pending"])

    async def test_reconciled_finished_chat_reply_backfills_completion_time(self) -> None:
        async with self.sessionmaker() as session:
            reply = {
                "id": "reply_done",
                "threadId": "executive",
                "role": "executive",
                "authorName": "ออตโต้",
                "text": "คำตอบเสร็จแล้ว",
                "ts": 1000,
                "pending": False,
                "status": "sent",
                "replyToMessageId": "user_1",
            }
            session.add(T.Message(id="reply_done", thread_id="executive", ts=1000, data=reply))
            session.add(T.Job(
                id="job_done",
                kind="chat_reply",
                status="done",
                run_after=1000,
                priority=1,
                payload={"replyMessageId": "reply_done"},
                attempts=0,
                last_error=None,
                created_at=1000,
                updated_at=6000,
            ))
            await session.commit()

        async with self.sessionmaker() as session:
            repo = Repo(session)
            repaired = await repo.reconcile_chat_reply_placeholders()
            newer = await repo.thread_messages_after("executive", after_ts=3000, after_id="later", limit=30)

        self.assertEqual(repaired, 1)
        self.assertEqual([m["id"] for m in newer], ["reply_done"])
        self.assertEqual(newer[0]["status"], "sent")
        self.assertEqual(newer[0]["ts"], 6000)
        self.assertEqual(newer[0]["completedAt"], 6000)
        self.assertFalse(newer[0]["pending"])

    async def test_all_threads_limits_threads_preserves_preferred_and_batches_messages(self) -> None:
        await self._seed_thread_messages()

        thread_messages_calls = 0

        async with self.sessionmaker() as session:
            repo = Repo(session)

            async def fail_if_n_plus_one_thread_fetch(thread_id: str, limit: int = 60) -> list[dict]:
                nonlocal thread_messages_calls
                del thread_id, limit
                thread_messages_calls += 1
                raise AssertionError("all_threads should batch-fetch thread messages")

            repo.thread_messages = fail_if_n_plus_one_thread_fetch  # type: ignore[method-assign]
            threads = await repo.all_threads(
                limit_per=2,
                max_threads=4,
                preferred_thread_ids=["old_preferred"],
            )

        self.assertEqual(list(threads), ["old_preferred", "newest", "recent_b", "recent_c"])
        self.assertEqual([msg["id"] for msg in threads["old_preferred"]], ["old_preferred_1", "old_preferred_2"])
        self.assertEqual([msg["id"] for msg in threads["newest"]], ["newest_1", "newest_2"])
        self.assertEqual(thread_messages_calls, 0)


if __name__ == "__main__":
    unittest.main()
