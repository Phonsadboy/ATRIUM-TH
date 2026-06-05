import unittest

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import tables as T
from app.db.base import Base
from app.db.repo import Repo, office_layout_context, office_layout_room_summaries


def _department(dept_id: str, name: str, created_at: int) -> dict:
    return {
        "id": dept_id,
        "name": name,
        "role": name,
        "charter": "",
        "emoji": "",
        "accent": "teal",
        "providerId": "claude_code",
        "model": "claude-sonnet-4-6",
        "thinkingEffort": "high",
        "speed": "standard",
        "agentName": name,
        "state": "idle",
        "mood": 0.5,
        "currentTaskId": None,
        "autonomy": dept_id == "exec",
        "createdAt": created_at,
        "room": {"x": 0, "y": 0, "w": 1, "h": 1},
        "memory": {
            "archiveChunks": 0,
            "ragEntries": 0,
            "graphNodes": 0,
            "graphEdges": 0,
            "lastCompactionAt": None,
            "tokensSaved": 0,
        },
        "skills": [],
        "tools": [],
    }


class OfficeLayoutTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _seed_departments(self) -> None:
        departments = [
            _department("exec", "ผู้บริหาร", 1),
            _department("dept_content", "Content", 2),
            _department("dept_sales", "Sales", 3),
            _department("dept_ops", "Ops", 4),
        ]
        async with self.sessionmaker() as session:
            for dept in departments:
                session.add(T.Department(id=dept["id"], created_at=dept["createdAt"], data=dept))
            await session.commit()

    async def test_default_layout_is_available_without_entity(self) -> None:
        await self._seed_departments()

        async with self.sessionmaker() as session:
            repo = Repo(session)
            layout = await repo.get_office_layout()
            snapshot = await repo.snapshot()

        self.assertEqual(layout["roomCount"], 1)
        self.assertEqual(layout["roomNames"], {})
        self.assertEqual(layout["departmentRooms"], {})
        self.assertEqual(layout["updatedAt"], 0)
        self.assertEqual(snapshot["officeLayout"], layout)

    async def test_patch_names_and_department_rooms_are_persisted_and_sanitized(self) -> None:
        await self._seed_departments()

        async with self.sessionmaker() as session:
            repo = Repo(session)
            layout = await repo.update_office_layout(
                {
                    "roomCount": 2,
                    "roomNames": {"1": " Content ", "bad": "Ignored index name"},
                    "departmentRooms": {
                        "dept_content": 1,
                        "dept_missing": 3,
                        "exec": 4,
                    },
                }
            )
            await session.commit()

        self.assertEqual(layout["roomCount"], 2)
        self.assertEqual(layout["roomNames"], {"1": "Content", "0": "Ignored index name"})
        self.assertEqual(layout["departmentRooms"], {"dept_content": 1})
        self.assertGreater(layout["updatedAt"], 0)

        async with self.sessionmaker() as session:
            persisted = await Repo(session).get_office_layout()

        self.assertEqual(persisted, layout)

    async def test_context_lists_departments_by_named_room(self) -> None:
        await self._seed_departments()

        async with self.sessionmaker() as session:
            repo = Repo(session)
            layout = await repo.update_office_layout(
                {
                    "roomCount": 2,
                    "roomNames": {"1": "ห้องคอนเทนต์"},
                    "departmentRooms": {"dept_content": 1, "dept_sales": 1},
                }
            )
            departments = await repo.list_departments()

        summaries = office_layout_room_summaries(layout, departments)
        content_room = summaries[1]
        self.assertEqual(content_room["title"], "ห้องคอนเทนต์")
        self.assertEqual([dept["id"] for dept in content_room["departments"]], ["dept_content", "dept_sales"])

        context = office_layout_context(layout, departments)
        self.assertIn("Office room layout for the AI executive:", context)
        self.assertIn("- ห้องคอนเทนต์: Content, Sales", context)
        self.assertIn("- ห้องผู้บริหาร:", context)

    async def test_layout_prunes_deleted_departments_on_read(self) -> None:
        await self._seed_departments()

        async with self.sessionmaker() as session:
            repo = Repo(session)
            await repo.update_office_layout(
                {
                    "roomCount": 2,
                    "departmentRooms": {"dept_content": 1, "dept_sales": 1},
                }
            )
            await session.commit()

        async with self.sessionmaker() as session:
            await session.execute(delete(T.Department).where(T.Department.id == "dept_content"))
            await session.commit()

        async with self.sessionmaker() as session:
            layout = await Repo(session).get_office_layout()

        self.assertEqual(layout["departmentRooms"], {"dept_sales": 1})


if __name__ == "__main__":
    unittest.main()
