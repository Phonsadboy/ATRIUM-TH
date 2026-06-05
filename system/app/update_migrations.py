"""Post-update schema and data migration runner.

This module is intentionally executable from a subprocess after `git merge` so
the updater can run migration code from the newly pulled checkout before
restarting the still-running backend process.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .clock import now_ms
from .db import init_db, session_scope
from .db.repo import Repo


MigrationFn = Callable[[AsyncSession], Awaitable[str | None]]


@dataclass(frozen=True)
class DataMigration:
    id: str
    summary: str
    run: MigrationFn


DATA_MIGRATIONS: tuple[DataMigration, ...] = ()


async def _ensure_data_migration_table(session: AsyncSession) -> None:
    await session.execute(text(
        'CREATE TABLE IF NOT EXISTS "atrium_data_migrations" ('
        '"id" VARCHAR(128) PRIMARY KEY, '
        '"applied_at" INTEGER NOT NULL, '
        '"summary" TEXT NOT NULL, '
        '"status" VARCHAR(24) NOT NULL'
        ')'
    ))


async def _applied_migration_ids(session: AsyncSession) -> set[str]:
    await _ensure_data_migration_table(session)
    rows = (await session.execute(text('SELECT "id" FROM "atrium_data_migrations" WHERE "status" = :status'), {"status": "applied"})).all()
    return {str(row[0]) for row in rows}


async def run_update_migrations() -> dict[str, Any]:
    await init_db()
    applied: list[dict[str, Any]] = []
    skipped: list[str] = []
    async with session_scope() as session:
        seen = await _applied_migration_ids(session)
        for migration in DATA_MIGRATIONS:
            if migration.id in seen:
                skipped.append(migration.id)
                continue
            detail = await migration.run(session)
            await session.execute(
                text(
                    'INSERT INTO "atrium_data_migrations" ("id", "applied_at", "summary", "status") '
                    'VALUES (:id, :applied_at, :summary, :status)'
                ),
                {
                    "id": migration.id,
                    "applied_at": now_ms(),
                    "summary": detail or migration.summary,
                    "status": "applied",
                },
            )
            applied.append({"id": migration.id, "summary": detail or migration.summary})
        schema = await Repo(session).database_schema_health()
    return {
        "ok": True,
        "schema": schema,
        "dataMigrations": {
            "available": len(DATA_MIGRATIONS),
            "applied": applied,
            "skipped": skipped,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run ATRIUM post-update schema and data migrations.")
    parser.add_argument("--json", action="store_true", help="print a JSON result")
    args = parser.parse_args()
    result = asyncio.run(run_update_migrations())
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(f"ok={result['ok']}")
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
