"""Add temporal validity/provenance to graph memory.

Revision ID: 20260530_0002
Revises: 20260530_0001
Create Date: 2026-05-30

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260530_0002"
down_revision: Union[str, None] = "20260530_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_column_if_missing(table: str, column: str, sql_type: str) -> None:
    op.execute(sa.text(
        f'ALTER TABLE atrium."{table}" ADD COLUMN IF NOT EXISTS "{column}" {sql_type}'
    ))


def upgrade() -> None:
    for table in ("graph_nodes", "graph_edges"):
        _add_column_if_missing(table, "valid_from", "BIGINT")
        _add_column_if_missing(table, "valid_to", "BIGINT")
        _add_column_if_missing(table, "confidence", "DOUBLE PRECISION DEFAULT 0.7")
        _add_column_if_missing(table, "source", "TEXT")
        op.execute(sa.text(
            f'CREATE INDEX IF NOT EXISTS "ix_{table}_valid_from" '
            f'ON atrium."{table}" ("valid_from")'
        ))
        op.execute(sa.text(
            f'CREATE INDEX IF NOT EXISTS "ix_{table}_valid_to" '
            f'ON atrium."{table}" ("valid_to")'
        ))


def downgrade() -> None:
    pass
