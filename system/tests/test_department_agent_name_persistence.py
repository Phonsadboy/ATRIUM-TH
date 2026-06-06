import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class DepartmentAgentNamePersistenceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from app.db.base import Base

        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_stale_runtime_save_does_not_revert_renamed_executive_to_default_name(self) -> None:
        from app.db.repo import AGENT_NAME_UPDATED_AT_KEY, Repo

        current = {
            "id": "exec",
            "name": "ผู้บริหาร",
            "agentName": "นีโอ",
            AGENT_NAME_UPDATED_AT_KEY: 2000,
            "state": "idle",
            "createdAt": 1,
        }
        stale_runtime_update = {
            **current,
            "agentName": "ออตโต้",
            AGENT_NAME_UPDATED_AT_KEY: 1000,
            "state": "working",
        }

        async with self.sessionmaker() as session:
            await Repo(session).save_department(current)
            await session.commit()

        async with self.sessionmaker() as session:
            await Repo(session).save_department(stale_runtime_update)
            await session.commit()

        async with self.sessionmaker() as session:
            persisted = await Repo(session).get_department("exec")

        self.assertEqual(persisted["agentName"], "นีโอ")
        self.assertEqual(persisted[AGENT_NAME_UPDATED_AT_KEY], 2000)
        self.assertEqual(persisted["state"], "working")

    async def test_explicit_agent_name_update_with_fresh_marker_is_persisted(self) -> None:
        from app.db.repo import AGENT_NAME_UPDATED_AT_KEY, Repo

        current = {
            "id": "exec",
            "name": "ผู้บริหาร",
            "agentName": "นีโอ",
            AGENT_NAME_UPDATED_AT_KEY: 2000,
            "state": "idle",
            "createdAt": 1,
        }
        rename = {
            **current,
            "agentName": "ริโอ",
            AGENT_NAME_UPDATED_AT_KEY: 2001,
        }

        async with self.sessionmaker() as session:
            await Repo(session).save_department(current)
            await session.commit()

        async with self.sessionmaker() as session:
            await Repo(session).save_department(rename)
            await session.commit()

        async with self.sessionmaker() as session:
            persisted = await Repo(session).get_department("exec")

        self.assertEqual(persisted["agentName"], "ริโอ")
        self.assertEqual(persisted[AGENT_NAME_UPDATED_AT_KEY], 2001)

    async def test_existing_unmarked_executive_rename_is_not_reverted_by_seed_default(self) -> None:
        from app.db.repo import Repo

        current = {
            "id": "exec",
            "name": "ผู้บริหาร",
            "agentName": "นีโอ",
            "state": "idle",
            "createdAt": 1,
        }
        stale_seed_default_update = {
            **current,
            "agentName": "ออตโต้",
            "state": "thinking",
        }

        async with self.sessionmaker() as session:
            await Repo(session).save_department(current)
            await session.commit()

        async with self.sessionmaker() as session:
            await Repo(session).save_department(stale_seed_default_update)
            await session.commit()

        async with self.sessionmaker() as session:
            persisted = await Repo(session).get_department("exec")

        self.assertEqual(persisted["agentName"], "นีโอ")
        self.assertEqual(persisted["state"], "thinking")


if __name__ == "__main__":
    unittest.main()
