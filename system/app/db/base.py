"""Async engine + session plumbing.

One schema serves both backends:
  * SQLite (default) — zero-setup, durable file under data_dir.
  * PostgreSQL — set ATRIUM_DATABASE_URL=postgresql+asyncpg://…

JSON columns use JSONB on Postgres and TEXT-backed JSON on SQLite via a
dialect variant, so the same ORM works everywhere.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from sqlalchemy import JSON, event, text
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from ..config import get_settings

# JSON that becomes JSONB on Postgres, plain JSON on SQLite.
JSONFlex = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None
SQLITE_SCHEMA_METADATA_TABLE = "atrium_schema_metadata"
SQLITE_SCHEMA_STAMP_VERSION = 1

_SQLITE_ADDITIVE_COLUMNS = {
    "tasks": {
        "project_id": "VARCHAR(64)",
    },
    "entities": {
        "dept": "VARCHAR(64)",
        "project": "VARCHAR(64)",
        "status": "VARCHAR(24)",
        "ts": "INTEGER DEFAULT 0",
    },
    "cost_records": {
        "provider_id": "VARCHAR(24)",
        "model": "VARCHAR(48)",
        "speed": "VARCHAR(16)",
        "tokens_in": "INTEGER",
        "tokens_out": "INTEGER",
    },
    "memory_knowledge": {
        "embedding": "JSON",
        "embedding_provider": "VARCHAR(64)",
        "embedding_model": "VARCHAR(128)",
        "embedding_dim": "INTEGER",
        "embedding_ts": "INTEGER",
        "source": "VARCHAR(64)",
    },
    "graph_nodes": {
        "valid_from": "INTEGER",
        "valid_to": "INTEGER",
        "confidence": "FLOAT DEFAULT 0.7",
        "source": "TEXT",
    },
    "graph_edges": {
        "valid_from": "INTEGER",
        "valid_to": "INTEGER",
        "confidence": "FLOAT DEFAULT 0.7",
        "source": "TEXT",
    },
    "jobs": {
        "priority": "INTEGER DEFAULT 5",
        "attempts": "INTEGER DEFAULT 0",
        "last_error": "TEXT",
        "created_at": "INTEGER DEFAULT 0",
        "updated_at": "INTEGER DEFAULT 0",
    },
}

_SQLITE_ADDITIVE_INDEXES = [
    ("ix_departments_created_at", "departments", ('"created_at"',)),
    ("ix_tasks_department_id", "tasks", ('"department_id"',)),
    ("ix_tasks_status", "tasks", ('"status"',)),
    ("ix_tasks_created_at", "tasks", ('"created_at"',)),
    ("ix_tasks_updated_at", "tasks", ('"updated_at"',)),
    ("ix_tasks_project_id", "tasks", ('"project_id"',)),
    ("ix_messages_thread_id", "messages", ('"thread_id"',)),
    ("ix_messages_ts", "messages", ('"ts"',)),
    ("ix_activity_ts", "activity", ('"ts"',)),
    ("ix_activity_department_id", "activity", ('"department_id"',)),
    ("ix_approvals_status", "approvals", ('"status"',)),
    ("ix_approvals_ts", "approvals", ('"ts"',)),
    ("ix_entities_dept", "entities", ('"dept"',)),
    ("ix_entities_project", "entities", ('"project"',)),
    ("ix_entities_status", "entities", ('"status"',)),
    ("ix_entities_ts", "entities", ('"ts"',)),
    ("ix_entities_type_ts", "entities", ('"type"', '"ts"')),
    ("ix_entities_type_status_ts", "entities", ('"type"', '"status"', '"ts"')),
    ("ix_entities_type_dept_ts", "entities", ('"type"', '"dept"', '"ts"')),
    ("ix_entities_type_dept_status_ts", "entities", ('"type"', '"dept"', '"status"', '"ts"')),
    (
        "ix_entities_type_dept_project_status_ts",
        "entities",
        ('"type"', '"dept"', '"project"', '"status"', '"ts"'),
    ),
    ("ix_cost_records_ts", "cost_records", ('"ts"',)),
    ("ix_cost_records_department_id", "cost_records", ('"department_id"',)),
    ("ix_cost_dept_ts", "cost_records", ('"department_id"', '"ts"')),
    ("ix_cost_category_ts", "cost_records", ('"category"', '"ts"')),
    ("ix_cost_dept_category_ts", "cost_records", ('"department_id"', '"category"', '"ts"')),
    ("ix_memory_archive_department_id", "memory_archive", ('"department_id"',)),
    ("ix_memory_archive_ts", "memory_archive", ('"ts"',)),
    ("ix_memory_knowledge_department_id", "memory_knowledge", ('"department_id"',)),
    ("ix_memory_knowledge_ts", "memory_knowledge", ('"ts"',)),
    ("ix_memory_knowledge_source", "memory_knowledge", ('"source"',)),
    ("ix_graph_nodes_department_id", "graph_nodes", ('"department_id"',)),
    ("ix_graph_nodes_valid_from", "graph_nodes", ('"valid_from"',)),
    ("ix_graph_nodes_valid_to", "graph_nodes", ('"valid_to"',)),
    ("ix_graph_edges_department_id", "graph_edges", ('"department_id"',)),
    ("ix_graph_edges_valid_from", "graph_edges", ('"valid_from"',)),
    ("ix_graph_edges_valid_to", "graph_edges", ('"valid_to"',)),
    ("ix_jobs_kind", "jobs", ('"kind"',)),
    ("ix_jobs_status", "jobs", ('"status"',)),
    ("ix_jobs_run_after", "jobs", ('"run_after"',)),
    ("ix_jobs_updated_at", "jobs", ('"updated_at"',)),
    ("ix_jobs_ready", "jobs", ('"status"', '"run_after"')),
]


def _sqlite_database_path(url: str) -> str | None:
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite":
        return None
    database = parsed.database
    if not database or database == ":memory:":
        return None
    return database


def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        # Background chat jobs and the live UI can write at the same time
        # during local SQLite development. Wait long enough for short UI writes
        # instead of failing the model reply with "database is locked".
        cursor.execute("PRAGMA busy_timeout=30000")
    finally:
        cursor.close()


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        url = settings.effective_database_url
        kwargs: dict = {"future": True}
        if url.startswith("sqlite"):
            db_path = _sqlite_database_path(url)
            if db_path:
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            # allow cross-task use of the connection in the async engine
            kwargs["connect_args"] = {"check_same_thread": False}
        else:
            kwargs["pool_size"] = 10
            kwargs["pool_pre_ping"] = True
            kwargs["connect_args"] = {"server_settings": {"search_path": "atrium,public"}}
        _engine = create_async_engine(url, **kwargs)
        if url.startswith("sqlite"):
            event.listen(_engine.sync_engine, "connect", _configure_sqlite_connection)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False, class_=AsyncSession)
    return _sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    sm = get_sessionmaker()
    async with sm() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def commit_and_release(session: AsyncSession) -> None:
    """Commit pending work and return the checked-out connection to the pool."""
    await session.commit()
    await session.close()


async def init_db() -> None:
    """Create tables and (on SQLite) enable WAL for concurrent readers."""
    from . import tables  # noqa: F401  (register models on Base.metadata)

    settings = get_settings()
    if settings.is_postgres:
        engine = get_engine()
        async with engine.begin() as conn:
            migrations_current = await _postgres_migrations_current(conn)
        if not migrations_current:
            await _run_postgres_migrations()
        async with engine.begin() as conn:
            vector_ready = False
            try:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                vector_ready = True
            except Exception:
                try:
                    vector_ready = bool((await conn.execute(text("SELECT to_regtype('vector') IS NOT NULL"))).scalar())
                except Exception:
                    vector_ready = False
            if vector_ready:
                await _ensure_postgres_vector_schema(conn)
        return

    engine = get_engine()
    async with engine.begin() as conn:
        vector_ready = False
        if settings.is_postgres:
            # pgvector is optional; ignore if the extension isn't installed.
            try:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                vector_ready = True
            except Exception:
                try:
                    vector_ready = bool((await conn.execute(text("SELECT to_regtype('vector') IS NOT NULL"))).scalar())
                except Exception:
                    vector_ready = False
        await conn.run_sync(Base.metadata.create_all)
        if settings.is_postgres and vector_ready:
            await _ensure_postgres_vector_schema(conn)

    if not settings.is_postgres:
        async with engine.begin() as conn:
            await _ensure_sqlite_additive_schema(conn)
            await _write_sqlite_schema_stamp(conn)
            await conn.execute(text("PRAGMA busy_timeout=30000"))


def _latest_postgres_migration_revision() -> str | None:
    versions_dir = Path(__file__).resolve().parents[2] / "alembic" / "versions"
    revisions: list[str] = []
    for path in versions_dir.glob("*.py"):
        if path.name.startswith("__"):
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped.startswith("revision"):
                    continue
                _, raw = stripped.split("=", 1)
                revisions.append(raw.strip().strip('"\''))
                break
        except OSError:
            continue
    return sorted(revisions)[-1] if revisions else None


async def _postgres_migrations_current(conn) -> bool:
    head = _latest_postgres_migration_revision()
    if not head:
        return False
    exists = bool((await conn.execute(text("SELECT to_regclass('alembic_version') IS NOT NULL"))).scalar())
    if not exists:
        return False
    current = (await conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))).scalar()
    return str(current or "") == head


async def _run_postgres_migrations() -> None:
    """Run Alembic migrations for Postgres without blocking the app event loop."""
    def _upgrade() -> None:
        from alembic import command
        from alembic.config import Config

        system_dir = Path(__file__).resolve().parents[2]
        cfg = Config(str(system_dir / "alembic.ini"))
        cfg.set_main_option("script_location", str(system_dir / "alembic"))
        command.upgrade(cfg, "head")

    await asyncio.to_thread(_upgrade)


async def _sqlite_columns(conn, table_name: str) -> set[str]:
    rows = (await conn.execute(text(f'PRAGMA table_info("{table_name}")'))).all()
    return {str(row[1]) for row in rows}


async def _sqlite_table_names(conn) -> set[str]:
    rows = (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).all()
    return {str(row[0]) for row in rows}


async def _sqlite_index_names(conn, table_name: str) -> set[str]:
    rows = (await conn.execute(text(f'PRAGMA index_list("{table_name}")'))).all()
    return {str(row[1]) for row in rows}


def _expected_sqlite_schema_payload() -> dict[str, Any]:
    from . import tables  # noqa: F401  (register models on Base.metadata)

    dialect = sqlite_dialect.dialect()
    payload: dict[str, Any] = {"stampVersion": SQLITE_SCHEMA_STAMP_VERSION, "tables": {}}
    for table in sorted(Base.metadata.tables.values(), key=lambda item: item.name):
        payload["tables"][table.name] = {
            "columns": [
                {
                    "name": column.name,
                    "type": column.type.compile(dialect=dialect),
                    "primaryKey": bool(column.primary_key),
                }
                for column in table.columns
            ],
            "indexes": sorted(
                ({
                    "name": index.name,
                    "columns": [column.name for column in index.columns],
                }
                for index in table.indexes
                if index.name
                ),
                key=lambda item: str(item["name"]),
            ),
        }
    return payload


def _schema_fingerprint(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


async def _actual_sqlite_schema_payload(conn) -> dict[str, Any]:
    expected = _expected_sqlite_schema_payload()
    existing_tables = await _sqlite_table_names(conn)
    payload: dict[str, Any] = {"stampVersion": SQLITE_SCHEMA_STAMP_VERSION, "tables": {}}
    for table_name in expected["tables"]:
        if table_name not in existing_tables:
            continue
        columns = []
        for row in (await conn.execute(text(f'PRAGMA table_info("{table_name}")'))).all():
            columns.append({
                "name": str(row[1]),
                "type": str(row[2] or ""),
                "primaryKey": bool(row[5]),
            })
        index_names = await _sqlite_index_names(conn, table_name)
        payload["tables"][table_name] = {
            "columns": columns,
            "indexes": sorted(name for name in index_names if not name.startswith("sqlite_autoindex_")),
        }
    return payload


def _schema_missing_items(expected: dict[str, Any], actual: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    missing_tables: list[str] = []
    missing_columns: list[dict[str, Any]] = []
    missing_indexes: list[dict[str, Any]] = []
    actual_tables = actual.get("tables") or {}
    for table_name, expected_table in (expected.get("tables") or {}).items():
        actual_table = actual_tables.get(table_name)
        if not isinstance(actual_table, dict):
            missing_tables.append(table_name)
            continue
        actual_columns = {str(column.get("name")) for column in actual_table.get("columns") or [] if isinstance(column, dict)}
        for column in expected_table.get("columns") or []:
            column_name = str(column.get("name"))
            if column_name not in actual_columns:
                missing_columns.append({"table": table_name, "column": column_name})
        actual_indexes = {
            str(index.get("name") if isinstance(index, dict) else index)
            for index in actual_table.get("indexes") or []
        }
        for index in expected_table.get("indexes") or []:
            index_name = str(index.get("name"))
            if index_name not in actual_indexes:
                missing_indexes.append({"table": table_name, "index": index_name})
    return missing_tables, missing_columns, missing_indexes


async def _ensure_sqlite_schema_metadata_table(conn) -> None:
    await conn.execute(text(
        f'CREATE TABLE IF NOT EXISTS "{SQLITE_SCHEMA_METADATA_TABLE}" ('
        '"key" TEXT PRIMARY KEY, '
        '"value" TEXT NOT NULL, '
        '"updated_at" INTEGER NOT NULL'
        ')'
    ))


async def _sqlite_schema_metadata(conn) -> dict[str, str]:
    if SQLITE_SCHEMA_METADATA_TABLE not in await _sqlite_table_names(conn):
        return {}
    rows = (await conn.execute(text(f'SELECT "key", "value" FROM "{SQLITE_SCHEMA_METADATA_TABLE}"'))).all()
    return {str(row[0]): str(row[1]) for row in rows}


async def _write_sqlite_schema_stamp(conn) -> None:
    await _ensure_sqlite_schema_metadata_table(conn)
    status = await sqlite_schema_status(conn, include_metadata=False)
    now = int(time.time() * 1000)
    values = {
        "stampVersion": str(SQLITE_SCHEMA_STAMP_VERSION),
        "expectedFingerprint": status["expectedFingerprint"],
        "actualFingerprint": status["actualFingerprint"],
        "matchesExpected": "true" if status["matchesExpected"] else "false",
    }
    for key, value in values.items():
        await conn.execute(
            text(
                f'INSERT INTO "{SQLITE_SCHEMA_METADATA_TABLE}" ("key", "value", "updated_at") '
                'VALUES (:key, :value, :updated_at) '
                'ON CONFLICT("key") DO UPDATE SET "value" = excluded."value", "updated_at" = excluded."updated_at"'
            ),
            {"key": key, "value": value, "updated_at": now},
        )


async def sqlite_schema_status(conn, *, include_metadata: bool = True) -> dict[str, Any]:
    expected = _expected_sqlite_schema_payload()
    actual = await _actual_sqlite_schema_payload(conn)
    missing_tables, missing_columns, missing_indexes = _schema_missing_items(expected, actual)
    expected_fingerprint = _schema_fingerprint(expected)
    actual_fingerprint = _schema_fingerprint(actual)
    metadata = await _sqlite_schema_metadata(conn) if include_metadata else {}
    return {
        "backend": "sqlite",
        "stampVersion": SQLITE_SCHEMA_STAMP_VERSION,
        "expectedFingerprint": expected_fingerprint,
        "actualFingerprint": actual_fingerprint,
        "matchesExpected": not missing_tables and not missing_columns and not missing_indexes,
        "stampMatchesExpected": metadata.get("expectedFingerprint") == expected_fingerprint if metadata else False,
        "stampedActualFingerprint": metadata.get("actualFingerprint"),
        "stampedMatchesExpected": metadata.get("matchesExpected"),
        "missingTables": missing_tables,
        "missingColumns": missing_columns[:50],
        "missingIndexes": missing_indexes[:50],
        "limitation": "SQLite migrations remain additive-only; type, NOT NULL, rename, and drop changes require an explicit migration.",
    }


async def _ensure_sqlite_additive_schema(conn) -> None:
    """Backfill nullable columns that `create_all()` cannot add to old SQLite DBs."""
    for table_name, columns in _SQLITE_ADDITIVE_COLUMNS.items():
        existing = await _sqlite_columns(conn, table_name)
        if not existing:
            continue
        for column_name, column_sql in columns.items():
            if column_name in existing:
                continue
            await conn.execute(text(f'ALTER TABLE "{table_name}" ADD COLUMN "{column_name}" {column_sql}'))
            existing.add(column_name)

    for index_name, table_name, columns in _SQLITE_ADDITIVE_INDEXES:
        existing = await _sqlite_columns(conn, table_name)
        if not all(column.strip('"') in existing for column in columns):
            continue
        column_sql = ", ".join(columns)
        await conn.execute(text(f'CREATE INDEX IF NOT EXISTS "{index_name}" ON "{table_name}" ({column_sql})'))


async def _ensure_postgres_vector_schema(conn) -> None:
    """Best-effort pgvector column for semantic retrieval when Postgres has pgvector."""
    dim = int(get_settings().embedding_dim)
    for column_sql in (
        'ADD COLUMN IF NOT EXISTS embedding_provider VARCHAR(64)',
        'ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(128)',
        'ADD COLUMN IF NOT EXISTS embedding_dim INTEGER',
        'ADD COLUMN IF NOT EXISTS embedding_ts BIGINT',
    ):
        await conn.execute(text(f'ALTER TABLE "memory_knowledge" {column_sql}'))
    try:
        await conn.execute(text(f'ALTER TABLE "memory_knowledge" ADD COLUMN IF NOT EXISTS embedding_vector vector({dim})'))
    except Exception:
        return
    try:
        async with conn.begin_nested():
            await conn.execute(text(
                'CREATE INDEX IF NOT EXISTS "ix_memory_knowledge_embedding_vector" '
                'ON "memory_knowledge" USING ivfflat (embedding_vector vector_cosine_ops)'
            ))
    except Exception:
        # Some pgvector versions/configs require fixed-dimension expression
        # indexes. Sequential vector search still works without this index.
        pass


async def dispose_db() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
