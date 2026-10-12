"""diagnostics: the log AgentLab keeps for issue reports, and what a person collected for one

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-12 23:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _entity() -> list[sa.Column]:
    return [
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "log_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("level", sa.String(length=10), nullable=False),
        sa.Column("logger", sa.String(length=120), nullable=False),
        sa.Column("process", sa.String(length=12), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.String(length=40), nullable=True),
        sa.Column("principal", sa.String(length=80), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("exception", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("ts", "process", "run_id", "request_id", "principal"):
        op.create_index(f"ix_log_events_{column}", "log_events", [column])

    op.create_table(
        "diagnostic_collections",
        sa.Column("owner_user_id", sa.String(length=36), nullable=True),
        sa.Column("owner_label", sa.String(length=80), nullable=False),
        sa.Column("submission_id", sa.String(length=36), nullable=True),
        sa.Column("requested", JSON, nullable=False),
        sa.Column("window_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consent_by", sa.String(length=80), nullable=False),
        *_entity(),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_diagnostic_collections_owner_user_id", "diagnostic_collections", ["owner_user_id"])
    op.create_index("ix_diagnostic_collections_submission_id", "diagnostic_collections", ["submission_id"])

    op.create_table(
        "diagnostic_items",
        sa.Column("collection_id", sa.String(length=36), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("path", sa.String(length=120), nullable=False),
        sa.Column("source", sa.String(length=120), nullable=False),
        sa.Column("media_type", sa.String(length=60), nullable=False),
        sa.Column("time_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("time_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("detail", sa.String(length=300), nullable=False),
        sa.Column("artifact_id", sa.String(length=80), nullable=True),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False),
        sa.Column("included", sa.Boolean(), nullable=False),
        sa.Column("redaction_status", sa.String(length=16), nullable=False),
        sa.Column("redaction_report", JSON, nullable=False),
        *_entity(),
        sa.ForeignKeyConstraint(["collection_id"], ["diagnostic_collections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_diagnostic_items_collection_id", "diagnostic_items", ["collection_id"])
    op.create_index("ix_diagnostic_items_category", "diagnostic_items", ["category"])


def downgrade() -> None:
    for name in ("diagnostic_items", "diagnostic_collections", "log_events"):
        op.drop_table(name)
