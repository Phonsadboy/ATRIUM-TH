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


if __name__ == "__main__":
    unittest.main()
