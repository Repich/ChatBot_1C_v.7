"""Persist page continuations and confirmed maintenance operations.

Revision ID: 0003_slice2
Revises: 0002_context_fact_origin
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_slice2"
down_revision = "0002_context_fact_origin"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "page_continuations",
        sa.Column("handle", sa.String(100), primary_key=True),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("origin_turn_id", sa.String(36), nullable=False),
        sa.Column("step_id", sa.String(80), nullable=False),
        sa.Column("skill_id", sa.String(120), nullable=False),
        sa.Column("skill_version", sa.String(80), nullable=False),
        sa.Column("skill_digest", sa.String(64), nullable=False),
        sa.Column("catalog_snapshot_id", sa.String(36), nullable=False),
        sa.Column("catalog_revision", sa.Integer(), nullable=False),
        sa.Column("normalized_params_digest", sa.String(64), nullable=False),
        sa.Column("arguments_json", sa.Text(), nullable=False),
        sa.Column("plan_json", sa.Text(), nullable=False),
        sa.Column("strategy", sa.String(20), nullable=False),
        sa.Column("page_size", sa.Integer(), nullable=False),
        sa.Column("shown", sa.Integer(), nullable=False),
        sa.Column("database_marker", sa.String(64), nullable=False),
        sa.Column("sort_tuple_json", sa.Text(), nullable=False),
        sa.Column("cursor_values_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("expires_at", sa.String(40), nullable=False),
        sa.Column("consumed_at", sa.String(40), nullable=True),
        sa.Column("accepted_turn_id", sa.String(36), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id"], ["sessions.session_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["origin_turn_id"], ["turns.turn_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["accepted_turn_id"], ["turns.turn_id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_page_continuations_session", "page_continuations", ["session_id"]
    )
    op.create_table(
        "maintenance_previews",
        sa.Column("token", sa.String(100), primary_key=True),
        sa.Column("scopes_json", sa.Text(), nullable=False),
        sa.Column("counts_json", sa.Text(), nullable=False),
        sa.Column("target_fingerprint", sa.String(64), nullable=False),
        sa.Column("issued_at", sa.String(40), nullable=False),
        sa.Column("expires_at", sa.String(40), nullable=False),
        sa.Column("consumed_at", sa.String(40), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("maintenance_previews")
    op.drop_index("ix_page_continuations_session", table_name="page_continuations")
    op.drop_table("page_continuations")
