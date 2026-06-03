"""Copy ATRIUM data from the local SQLite DB into Postgres.

This is an operational migration helper for Phase 0 cutover. It preserves row
IDs and JSON payloads, initializes pgvector when available, and backfills the
best-effort vector column from stored JSON embeddings.
"""
from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.db.base import Base
from app.db import tables as T
from app.memory.embeddings import resolve_embedder


MODELS: Sequence[type[Base]] = (
    T.Company,
    T.Department,
    T.Task,
    T.Message,
    T.Activity,
    T.Approval,
    T.Objective,
    T.CostRecordRow,
    T.MemoryArchive,
    T.MemoryKnowledge,
    T.GraphNode,
    T.GraphEdge,
    T.Job,
    T.Entity,
)


def _vector_literal(vec: list[Any]) -> str:
    values = []
    for value in vec:
        try:
            values.append(format(float(value), ".9g"))
        except Exception:
            values.append("0")
    return "[" + ",".join(values) + "]"


def _row_data(row: Any) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


async def _prepare_target(engine: AsyncEngine, *, replace: bool) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        if replace:
            await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text('ALTER TABLE "memory_knowledge" ADD COLUMN IF NOT EXISTS embedding_vector vector'))


async def _try_create_vector_index(engine: AsyncEngine) -> None:
    try:
        async with engine.begin() as conn:
            await conn.execute(text(
                'CREATE INDEX IF NOT EXISTS "ix_memory_knowledge_embedding_vector" '
                'ON "memory_knowledge" USING ivfflat (embedding_vector vector_cosine_ops)'
            ))
    except Exception:
        # Sequential pgvector search still works; some pgvector versions
        # require fixed dimensions for ivfflat indexes.
        pass


async def _copy_table(source: AsyncSession, target: AsyncSession, model: type[Base]) -> int:
    rows = (await source.execute(select(model))).scalars().all()
    if not rows:
        return 0
    target.add_all(model(**_row_data(row)) for row in rows)
    await target.flush()
    return len(rows)


async def _backfill_vectors_from_stored_json(target: AsyncSession) -> int:
    rows = (
        await target.execute(
            select(T.MemoryKnowledge.id, T.MemoryKnowledge.embedding).where(T.MemoryKnowledge.embedding.is_not(None))
        )
    ).all()
    count = 0
    for knowledge_id, embedding in rows:
        if not isinstance(embedding, list) or not embedding:
            continue
        await target.execute(
            text('UPDATE "memory_knowledge" SET embedding_vector = CAST(:embedding AS vector) WHERE id = :id'),
            {"id": knowledge_id, "embedding": _vector_literal(embedding)},
        )
        count += 1
    return count


async def _reembed_knowledge(target: AsyncSession, *, allow_fallback: bool, batch_size: int = 16) -> dict[str, Any]:
    embedder = await resolve_embedder()
    if embedder.name.startswith("hash-") and not allow_fallback:
        raise RuntimeError("refusing to migrate knowledge with hash embeddings; start Ollama bge-m3 or pass --allow-fallback-embeddings")
    rows = (
        await target.execute(
            select(T.MemoryKnowledge.id, T.MemoryKnowledge.title, T.MemoryKnowledge.text)
            .order_by(T.MemoryKnowledge.ts, T.MemoryKnowledge.id)
        )
    ).all()
    count = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        texts = [f"{title}\n\n{text}".strip() for _, title, text in batch]
        vectors = await embedder.embed(texts)
        if len(vectors) != len(batch):
            raise RuntimeError(f"embedder returned {len(vectors)} vectors for {len(batch)} knowledge rows")
        for (knowledge_id, _title, _text), vector in zip(batch, vectors):
            await target.execute(
                update(T.MemoryKnowledge).where(T.MemoryKnowledge.id == knowledge_id).values(embedding=vector)
            )
            await target.execute(
                text('UPDATE "memory_knowledge" SET embedding_vector = CAST(:embedding AS vector) WHERE id = :id'),
                {"id": knowledge_id, "embedding": _vector_literal(vector)},
            )
            count += 1
    return {"provider": embedder.name, "dim": getattr(embedder, "dim", None), "count": count}


async def _reset_sequences(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(
            "SELECT setval(pg_get_serial_sequence('graph_edges', 'pk'), "
            "GREATEST((SELECT COALESCE(MAX(pk), 1) FROM graph_edges), 1), true)"
        ))


async def migrate(
    sqlite_url: str,
    postgres_url: str,
    *,
    replace: bool,
    reembed: bool,
    allow_fallback_embeddings: bool,
) -> dict[str, Any]:
    source_engine = create_async_engine(sqlite_url, future=True)
    target_engine = create_async_engine(postgres_url, future=True)
    try:
        await _prepare_target(target_engine, replace=replace)
        counts: dict[str, int] = {}
        async with AsyncSession(source_engine, expire_on_commit=False) as source:
            async with AsyncSession(target_engine, expire_on_commit=False) as target:
                for model in MODELS:
                    counts[model.__tablename__] = await _copy_table(source, target, model)
                if reembed:
                    counts["memory_knowledge.reembedded"] = await _reembed_knowledge(
                        target,
                        allow_fallback=allow_fallback_embeddings,
                    )
                else:
                    counts["memory_knowledge.embedding_vector"] = await _backfill_vectors_from_stored_json(target)
                await target.commit()
        await _reset_sequences(target_engine)
        await _try_create_vector_index(target_engine)
        return {"ok": True, "replace": replace, "counts": counts}
    finally:
        await source_engine.dispose()
        await target_engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sqlite-path",
        default=str(Path("system/data/atrium.db")),
        help="Path to the source SQLite database.",
    )
    parser.add_argument(
        "--postgres-url",
        default="postgresql+asyncpg://atrium:atrium@127.0.0.1:5432/atrium",
        help="Target SQLAlchemy Postgres URL.",
    )
    parser.add_argument("--replace", action="store_true", help="Delete existing target rows before copying.")
    parser.add_argument(
        "--skip-reembed",
        action="store_true",
        help="Copy stored JSON embeddings instead of re-embedding knowledge with the configured embedder.",
    )
    parser.add_argument(
        "--allow-fallback-embeddings",
        action="store_true",
        help="Allow migration to continue with hash embeddings if Ollama/Voyage are unavailable.",
    )
    args = parser.parse_args()
    sqlite_path = Path(args.sqlite_path).resolve()
    sqlite_url = f"sqlite+aiosqlite:///{sqlite_path}"
    result = asyncio.run(
        migrate(
            sqlite_url,
            args.postgres_url,
            replace=args.replace,
            reembed=not args.skip_reembed,
            allow_fallback_embeddings=args.allow_fallback_embeddings,
        )
    )
    print(result)


if __name__ == "__main__":
    main()
