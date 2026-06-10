"""ATRIUM baseline schema (v2 Phase 0).

Revision ID: 20260530_0001
Revises:
Create Date: 2026-05-30

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260530_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _try_execute(bind, sql: str) -> bool:
    tx = bind.begin_nested()
    try:
        bind.execute(sa.text(sql))
    except Exception:
        tx.rollback()
        return False
    tx.commit()
    return True


def _pgvector_ready(bind) -> bool:
    if _try_execute(bind, "CREATE EXTENSION IF NOT EXISTS vector"):
        return True
    tx = bind.begin_nested()
    try:
        ready = bool(bind.execute(sa.text("SELECT to_regtype('vector') IS NOT NULL")).scalar())
    except Exception:
        tx.rollback()
        return False
    tx.commit()
    return ready


def upgrade() -> None:
    from app.db import tables  # noqa: F401  (register models on Base.metadata)
    from app.db.base import Base

    bind = op.get_bind()
    op.execute("CREATE SCHEMA IF NOT EXISTS atrium")
    vector_ready = _pgvector_ready(bind)
    for table in Base.metadata.tables.values():
        table.schema = "atrium"
    for table in Base.metadata.sorted_tables:
        bind.execute(sa.schema.CreateTable(table, if_not_exists=True))
    for table in Base.metadata.sorted_tables:
        for index in table.indexes:
            bind.execute(sa.schema.CreateIndex(index, if_not_exists=True))

    # Existing local installs created v1 tables in `public`. Preserve that data
    # while moving the live runtime to the dedicated `atrium` schema.
    for table in Base.metadata.sorted_tables:
        public_exists = bind.execute(
            sa.text("SELECT to_regclass(:table_name) IS NOT NULL"),
            {"table_name": f"public.{table.name}"},
        ).scalar()
        if not public_exists:
            continue
        cols = ", ".join(f'"{column.name}"' for column in table.columns)
        bind.execute(sa.text(
            f'INSERT INTO atrium."{table.name}" ({cols}) '
            f'SELECT {cols} FROM public."{table.name}" '
            "ON CONFLICT DO NOTHING"
        ))
    bind.execute(sa.text(
        "SELECT setval(pg_get_serial_sequence('atrium.graph_edges', 'pk'), "
        "GREATEST(COALESCE((SELECT MAX(pk) FROM atrium.graph_edges), 1), 1), true)"
    ))
    if vector_ready:
        if _try_execute(bind, 'ALTER TABLE atrium."memory_knowledge" ADD COLUMN IF NOT EXISTS embedding_vector vector(1024)'):
            public_vector_column = bind.execute(sa.text("""
                SELECT EXISTS (
                  SELECT 1
                  FROM information_schema.columns
                  WHERE table_schema = 'public'
                    AND table_name = 'memory_knowledge'
                    AND column_name = 'embedding_vector'
                )
            """)).scalar()
            if public_vector_column:
                bind.execute(sa.text("""
                    UPDATE atrium."memory_knowledge" AS target
                    SET embedding_vector = source.embedding_vector
                    FROM public."memory_knowledge" AS source
                    WHERE target.id = source.id
                      AND source.embedding_vector IS NOT NULL
                      AND target.embedding_vector IS NULL
                """))
            _try_execute(
                bind,
                'CREATE INDEX IF NOT EXISTS "ix_memory_knowledge_embedding_vector" '
                'ON atrium."memory_knowledge" USING ivfflat (embedding_vector vector_cosine_ops)',
            )


def downgrade() -> None:
    pass
