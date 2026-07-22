"""Add proof-bearing context slots and persisted clarification state.

Revision ID: 0004_entity_context
Revises: 0003_slice2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_entity_context"
down_revision = "0003_slice2"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_index("ix_context_facts_session", table_name="context_facts")
    op.rename_table("context_facts", "context_facts_legacy")
    op.create_table(
        "context_facts",
        sa.Column("fact_record_id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("semantic_type", sa.String(160), nullable=False),
        sa.Column("value_type", sa.String(40), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("value_digest", sa.String(64), nullable=False),
        sa.Column("presentation", sa.String(500), nullable=False),
        sa.Column("origin_turn_id", sa.String(36), nullable=False),
        sa.Column("origin_fact_instance_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"], ["sessions.session_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["origin_turn_id"], ["turns.turn_id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("session_id", "origin_fact_instance_id"),
    )
    op.create_index(
        "ix_context_facts_session", "context_facts", ["session_id", "created_at"]
    )
    op.create_table(
        "context_slots",
        sa.Column("handle", sa.String(100), primary_key=True),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("slot_key", sa.String(160), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("semantic_type", sa.String(160), nullable=False),
        sa.Column("value_type", sa.String(40), nullable=False),
        sa.Column("policy_mode", sa.String(40), nullable=False),
        sa.Column("cardinality", sa.String(20), nullable=False),
        sa.Column("member_count", sa.Integer(), nullable=False),
        sa.Column("membership_digest", sa.String(64), nullable=False),
        sa.Column("presentation", sa.String(500), nullable=False),
        sa.Column("lifetime_mode", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.String(40), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("reason", sa.String(80), nullable=True),
        sa.Column("replaced_by", sa.String(100), nullable=True),
        sa.Column("proof_digest", sa.String(64), nullable=True),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"], ["sessions.session_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["replaced_by"], ["context_slots.handle"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("session_id", "slot_key", "generation"),
    )
    op.create_index("ix_context_slots_session", "context_slots", ["session_id"])
    op.execute(
        "CREATE UNIQUE INDEX ux_context_slots_active "
        "ON context_slots(session_id, slot_key) WHERE status='active'"
    )
    op.create_table(
        "context_slot_members",
        sa.Column("handle", sa.String(100), nullable=False),
        sa.Column("member_index", sa.Integer(), nullable=False),
        sa.Column("fact_record_id", sa.String(36), nullable=False),
        sa.Column("entity_identity_digest", sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(
            ["handle"], ["context_slots.handle"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["fact_record_id"], ["context_facts.fact_record_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("handle", "member_index"),
        sa.UniqueConstraint("handle", "fact_record_id"),
    )
    op.create_table(
        "pending_clarifications",
        sa.Column("handle", sa.String(100), primary_key=True),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("origin_turn_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("question_ru", sa.Text(), nullable=False),
        sa.Column("original_question", sa.Text(), nullable=False),
        sa.Column("plan_json", sa.Text(), nullable=False),
        sa.Column("resolver_step_id", sa.String(80), nullable=True),
        sa.Column("choices_json", sa.Text(), nullable=False),
        sa.Column("has_more_candidates", sa.Boolean(), nullable=False),
        sa.Column("context_version", sa.Integer(), nullable=False),
        sa.Column("catalog_snapshot_id", sa.String(36), nullable=False),
        sa.Column("catalog_revision", sa.Integer(), nullable=False),
        sa.Column("database_marker", sa.String(64), nullable=False),
        sa.Column("issued_at", sa.String(40), nullable=False),
        sa.Column("expires_at", sa.String(40), nullable=False),
        sa.Column("consumed_at", sa.String(40), nullable=True),
        sa.Column("superseded_at", sa.String(40), nullable=True),
        sa.Column("claim_turn_id", sa.String(36), nullable=True),
        sa.Column("claimed_action", sa.String(20), nullable=True),
        sa.Column("claimed_choice_id", sa.String(20), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id"], ["sessions.session_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["origin_turn_id"], ["turns.turn_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["claim_turn_id"], ["turns.turn_id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_pending_clarifications_session",
        "pending_clarifications",
        ["session_id"],
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_pending_clarifications_active "
        "ON pending_clarifications(session_id) "
        "WHERE consumed_at IS NULL AND superseded_at IS NULL"
    )

    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT * FROM context_facts_legacy ORDER BY created_at, handle")
    ).mappings()
    generations: dict[str, int] = {}
    for row in rows:
        session_id = str(row["session_id"])
        generation = generations.get(session_id, 0) + 1
        generations[session_id] = generation
        value_digest = _sha(str(row["value_json"]).encode("utf-8"))
        fact_record_id = str(row["origin_fact_instance_id"])
        connection.execute(
            sa.text(
                "INSERT OR IGNORE INTO context_facts "
                "(fact_record_id,session_id,semantic_type,value_type,value_json,"
                "value_digest,presentation,origin_turn_id,origin_fact_instance_id,created_at) "
                "VALUES (:fact_record_id,:session_id,:semantic_type,'entity_ref',"
                ":value_json,:value_digest,:presentation,:origin_turn_id,"
                ":origin_fact_instance_id,:created_at)"
            ),
            {
                **dict(row),
                "fact_record_id": fact_record_id,
                "value_digest": value_digest,
            },
        )
        connection.execute(
            sa.text(
                "INSERT INTO context_slots "
                "(handle,session_id,slot_key,generation,semantic_type,value_type,"
                "policy_mode,cardinality,member_count,membership_digest,presentation,"
                "lifetime_mode,status,reason,created_at,updated_at) VALUES "
                "(:handle,:session_id,'legacy.selection_unproved',:generation,:semantic_type,"
                "'entity_ref','selected_only','one',1,:membership_digest,:presentation,"
                "'session','invalidated','migration_selection_unproven',:created_at,:created_at)"
            ),
            {
                **dict(row),
                "generation": generation,
                "membership_digest": value_digest,
            },
        )
        connection.execute(
            sa.text(
                "INSERT INTO context_slot_members (handle,member_index,fact_record_id) "
                "VALUES (:handle,0,:fact_record_id)"
            ),
            {"handle": row["handle"], "fact_record_id": fact_record_id},
        )
    op.drop_table("context_facts_legacy")


def downgrade() -> None:
    raise RuntimeError("0004 entity context migration is intentionally forward-only")


def _sha(value: bytes) -> str:
    import hashlib

    return hashlib.sha256(value).hexdigest()
