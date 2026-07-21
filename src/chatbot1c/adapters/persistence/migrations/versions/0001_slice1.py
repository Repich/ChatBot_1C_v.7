"""Initial slice 1 persistence schema.

Revision ID: 0001_slice1
Revises:
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_slice1"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "catalog_revisions",
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("snapshot_id", sa.String(36), nullable=False, unique=True),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("package_json", sa.Text(), nullable=True),
    )
    op.create_table(
        "skill_documents",
        sa.Column("skill_id", sa.String(120), nullable=False),
        sa.Column("version", sa.String(80), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("document_json", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("skill_id", "version"),
        sa.UniqueConstraint("digest"),
    )
    op.create_table(
        "catalog_revision_skills",
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("skill_id", sa.String(120), nullable=False),
        sa.Column("version", sa.String(80), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["revision"], ["catalog_revisions.revision"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["skill_id", "version"],
            ["skill_documents.skill_id", "skill_documents.version"],
        ),
        sa.PrimaryKeyConstraint("revision", "skill_id"),
    )
    op.create_table(
        "sessions",
        sa.Column("session_id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.Column("context_version", sa.Integer(), nullable=False),
    )
    op.create_table(
        "turns",
        sa.Column("turn_id", sa.String(36), primary_key=True),
        sa.Column("request_id", sa.String(36), nullable=False, unique=True),
        sa.Column("trace_id", sa.String(36), nullable=False, unique=True),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("client_message_id", sa.String(120), nullable=False),
        sa.Column("user_text", sa.Text(), nullable=False),
        sa.Column("assistant_text", sa.Text(), nullable=True),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("outcome", sa.String(80), nullable=True),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("completed_at", sa.String(40), nullable=True),
        sa.Column("context_version", sa.Integer(), nullable=False),
        sa.Column("catalog_snapshot_id", sa.String(36), nullable=True),
        sa.Column("catalog_revision", sa.Integer(), nullable=True),
        sa.Column("plan_json", sa.Text(), nullable=True),
        sa.Column("evidence_json", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id"], ["sessions.session_id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("session_id", "client_message_id"),
    )
    op.create_index("ix_turns_session_created", "turns", ["session_id", "created_at"])
    op.create_table(
        "turn_events",
        sa.Column("turn_id", sa.String(36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_name", sa.String(100), nullable=False),
        sa.Column("timestamp", sa.String(40), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["turn_id"], ["turns.turn_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("turn_id", "sequence"),
    )
    op.create_table(
        "context_facts",
        sa.Column("handle", sa.String(120), primary_key=True),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("semantic_type", sa.String(160), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("presentation", sa.String(500), nullable=False),
        sa.Column("origin_turn_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"], ["sessions.session_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["origin_turn_id"], ["turns.turn_id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_context_facts_session", "context_facts", ["session_id", "created_at"]
    )
    op.create_table(
        "trace_artifacts",
        sa.Column("trace_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.PrimaryKeyConstraint("trace_id", "name"),
    )
    op.create_table(
        "help_corpora",
        sa.Column("revision", sa.String(160), primary_key=True),
        sa.Column("corpus_id", sa.String(160), nullable=False),
        sa.Column("release", sa.String(32), nullable=False),
        sa.Column("manifest_digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "help_sources",
        sa.Column("source_id", sa.String(64), primary_key=True),
        sa.Column("revision", sa.String(160), nullable=False),
        sa.Column("relative_path", sa.String(800), nullable=False),
        sa.Column("metadata_kind", sa.String(40), nullable=False),
        sa.Column("metadata_object", sa.String(300), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["revision"], ["help_corpora.revision"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("revision", "relative_path"),
    )
    op.create_table(
        "help_chunks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("chunk_id", sa.String(160), nullable=False, unique=True),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("revision", sa.String(160), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("heading", sa.String(500), nullable=False),
        sa.Column("heading_path", sa.String(1000), nullable=False),
        sa.Column("anchor", sa.String(300), nullable=False),
        sa.Column("role", sa.String(40), nullable=False),
        sa.Column("plain_text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("chunk_sha256", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_id"], ["help_sources.source_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["revision"], ["help_corpora.revision"], ondelete="CASCADE"
        ),
    )
    op.create_index("ix_help_chunks_revision", "help_chunks", ["revision"])
    op.execute(
        "CREATE VIRTUAL TABLE help_chunks_fts USING fts5("
        "chunk_id UNINDEXED, title, heading, normalized_text, tokenize='unicode61')"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS help_chunks_fts")
    for table in (
        "help_chunks",
        "help_sources",
        "help_corpora",
        "trace_artifacts",
        "context_facts",
        "turn_events",
        "turns",
        "sessions",
        "catalog_revision_skills",
        "skill_documents",
        "catalog_revisions",
    ):
        op.drop_table(table)
