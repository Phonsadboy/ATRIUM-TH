"""Add knowledge embedding provenance metadata.

Revision ID: 20260530_0003
Revises: 20260530_0002
Create Date: 2026-05-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260530_0003"
down_revision: Union[str, None] = "20260530_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for schema in ("atrium", "public"):
        table_exists = op.get_bind().execute(
            sa.text("SELECT to_regclass(:table_name) IS NOT NULL"),
            {"table_name": f"{schema}.memory_knowledge"},
        ).scalar()
        if not table_exists:
            continue
        op.execute(sa.text(f'ALTER TABLE {schema}."memory_knowledge" ADD COLUMN IF NOT EXISTS embedding_provider VARCHAR(64)'))
        op.execute(sa.text(f'ALTER TABLE {schema}."memory_knowledge" ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(128)'))
        op.execute(sa.text(f'ALTER TABLE {schema}."memory_knowledge" ADD COLUMN IF NOT EXISTS embedding_dim INTEGER'))
        op.execute(sa.text(f'ALTER TABLE {schema}."memory_knowledge" ADD COLUMN IF NOT EXISTS embedding_ts BIGINT'))


def downgrade() -> None:
    for schema in ("atrium", "public"):
        table_exists = op.get_bind().execute(
            sa.text("SELECT to_regclass(:table_name) IS NOT NULL"),
            {"table_name": f"{schema}.memory_knowledge"},
        ).scalar()
        if not table_exists:
            continue
        op.drop_column("memory_knowledge", "embedding_ts", schema=schema)
        op.drop_column("memory_knowledge", "embedding_dim", schema=schema)
        op.drop_column("memory_knowledge", "embedding_model", schema=schema)
        op.drop_column("memory_knowledge", "embedding_provider", schema=schema)
