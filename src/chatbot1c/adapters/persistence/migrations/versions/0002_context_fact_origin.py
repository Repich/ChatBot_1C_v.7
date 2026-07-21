"""Persist immutable origin fact pointers for context bindings.

Revision ID: 0002_context_fact_origin
Revises: 0001_slice1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_context_fact_origin"
down_revision = "0001_slice1"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "context_facts",
        sa.Column("origin_fact_instance_id", sa.String(36), nullable=True),
    )
    op.execute(
        """
        UPDATE context_facts
        SET origin_fact_instance_id = (
            SELECT json_extract(export.value, '$.fact_instance_id')
            FROM turns AS origin_turn,
                 json_each(origin_turn.evidence_json, '$.context_exports') AS export
            WHERE origin_turn.turn_id = context_facts.origin_turn_id
              AND json_extract(export.value, '$.context_handle') = context_facts.handle
            LIMIT 1
        )
        """
    )
    # Legacy rows without immutable evidence cannot safely remain active context.
    op.execute("DELETE FROM context_facts WHERE origin_fact_instance_id IS NULL")
    with op.batch_alter_table("context_facts") as batch:
        batch.alter_column(
            "origin_fact_instance_id",
            existing_type=sa.String(36),
            nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("context_facts") as batch:
        batch.drop_column("origin_fact_instance_id")
