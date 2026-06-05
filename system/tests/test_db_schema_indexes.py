import unittest

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import tables as T
from app.db.base import (
    _SQLITE_ADDITIVE_INDEXES,
    _ensure_sqlite_additive_schema,
    _write_sqlite_schema_stamp,
    Base,
    SQLITE_SCHEMA_METADATA_TABLE,
    sqlite_schema_status,
)
from app.db.repo import Repo


class EntityCompositeIndexTest(unittest.TestCase):
    def test_entity_table_has_composite_indexes_for_list_entities_filters(self) -> None:
        indexes = {index.name: tuple(column.name for column in index.columns) for index in T.Entity.__table__.indexes}

        self.assertEqual(indexes["ix_entities_type_status_ts"], ("type", "status", "ts"))
        self.assertEqual(indexes["ix_entities_type_dept_ts"], ("type", "dept", "ts"))
        self.assertEqual(indexes["ix_entities_type_dept_status_ts"], ("type", "dept", "status", "ts"))
        self.assertEqual(
            indexes["ix_entities_type_dept_project_status_ts"],
            ("type", "dept", "project", "status", "ts"),
        )

    def test_sqlite_additive_indexes_include_entity_composites(self) -> None:
        indexes = {name: (table, columns) for name, table, columns in _SQLITE_ADDITIVE_INDEXES}

        self.assertEqual(indexes["ix_entities_type_status_ts"], ("entities", ('"type"', '"status"', '"ts"')))
        self.assertEqual(indexes["ix_entities_type_dept_ts"], ("entities", ('"type"', '"dept"', '"ts"')))
        self.assertEqual(
            indexes["ix_entities_type_dept_status_ts"],
            ("entities", ('"type"', '"dept"', '"status"', '"ts"')),
        )
        self.assertEqual(
            indexes["ix_entities_type_dept_project_status_ts"],
            ("entities", ('"type"', '"dept"', '"project"', '"status"', '"ts"')),
        )


class SQLiteSchemaStampTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await _ensure_sqlite_additive_schema(conn)
            await _write_sqlite_schema_stamp(conn)
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_sqlite_schema_stamp_records_current_fingerprints(self) -> None:
        async with self.engine.begin() as conn:
            status = await sqlite_schema_status(conn)
            rows = (
                await conn.execute(text(f'SELECT "key", "value" FROM "{SQLITE_SCHEMA_METADATA_TABLE}"'))
            ).all()

        metadata = {str(key): str(value) for key, value in rows}
        self.assertTrue(status["matchesExpected"])
        self.assertTrue(status["stampMatchesExpected"])
        self.assertEqual(metadata["expectedFingerprint"], status["expectedFingerprint"])
        self.assertEqual(metadata["actualFingerprint"], status["actualFingerprint"])
        self.assertEqual(metadata["matchesExpected"], "true")

    async def test_repo_database_schema_health_exposes_sqlite_status(self) -> None:
        async with self.sessionmaker() as session:
            health = await Repo(session).database_schema_health()

        self.assertEqual(health["backend"], "sqlite")
        self.assertTrue(health["matchesExpected"])
        self.assertTrue(health["stampMatchesExpected"])
        self.assertEqual(health["missingTables"], [])
