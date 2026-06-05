import unittest
from unittest import mock

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import repo as repo_module
from app.db import tables as T
from app.db.base import Base
from app.db.repo import Repo


class CostReportQueryTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_cost_report_anomaly_queries_are_bounded_to_window_start(self) -> None:
        day_ms = repo_module.DAY_MS
        today_start = 10 * day_ms
        now = today_start + (day_ms // 2)
        window_start = today_start - 6 * day_ms
        statements: list[str] = []

        def capture_sql(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement)

        event.listen(self.engine.sync_engine, "before_cursor_execute", capture_sql)
        try:
            async with self.sessionmaker() as session:
                session.add(
                    T.Department(
                        id="research",
                        created_at=1,
                        data={"id": "research", "name": "วิจัย", "emoji": "R"},
                    )
                )
                for idx in range(1, 7):
                    session.add(
                        T.CostRecordRow(
                            id=f"prior_{idx}",
                            ts=today_start - idx * day_ms + 1_000,
                            department_id="research",
                            category="work",
                            usd=1.0,
                        )
                    )
                session.add(
                    T.CostRecordRow(
                        id="today_high",
                        ts=today_start + 1_000,
                        department_id="research",
                        category="work",
                        usd=20.0,
                    )
                )
                session.add(
                    T.CostRecordRow(
                        id="old_history",
                        ts=window_start - day_ms,
                        department_id="research",
                        category="work",
                        usd=10_000.0,
                    )
                )
                await session.commit()

            async with self.sessionmaker() as session:
                with mock.patch.object(repo_module, "now_ms", return_value=now):
                    report = await Repo(session).cost_report("day")
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", capture_sql)

        self.assertTrue(any("ฝ่ายวิจัย" in item for item in report["anomalies"]))
        cost_selects = [
            " ".join(statement.split())
            for statement in statements
            if "FROM cost_records" in statement and statement.lstrip().upper().startswith("SELECT")
        ]
        self.assertGreaterEqual(len(cost_selects), 3)
        for statement in cost_selects:
            self.assertIn("cost_records.ts >=", statement)

    async def test_add_cost_normalizes_unknown_category_to_visible_tool_bucket(self) -> None:
        async with self.sessionmaker() as session:
            await Repo(session).add_cost(
                "cost_typo",
                123,
                "research",
                "toool",
                0.25,
                detail="bad category",
            )
            row = (await session.execute(select(T.CostRecordRow).where(T.CostRecordRow.id == "cost_typo"))).scalar_one()

        self.assertEqual(row.category, "tool")
        self.assertIn("rawCategory=toool", row.detail)

    async def test_cost_record_category_has_check_and_query_indexes(self) -> None:
        constraint_names = {constraint.name for constraint in T.CostRecordRow.__table__.constraints}
        index_names = {index.name for index in T.CostRecordRow.__table__.indexes}

        self.assertIn("ck_cost_records_category", constraint_names)
        self.assertIn("ix_cost_category_ts", index_names)
        self.assertIn("ix_cost_dept_category_ts", index_names)


if __name__ == "__main__":
    unittest.main()
