"""handoffs: the packages built from a submission for an engineer

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-13 01:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "engineering_handoffs",
        sa.Column("submission_id", sa.String(length=36), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_by_label", sa.String(length=80), nullable=False),
        sa.Column("submission_version", sa.Integer(), nullable=False),
        sa.Column("options", JSON, nullable=False),
        sa.Column("manifest", JSON, nullable=False),
        sa.Column("artifact_id", sa.String(length=80), nullable=False),
        sa.Column("task_artifact_id", sa.String(length=80), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("external_sharing_blocked", sa.Boolean(), nullable=False),
        sa.Column("includes_unsanitized", sa.Boolean(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_engineering_handoffs_submission_id", "engineering_handoffs", ["submission_id"])


def downgrade() -> None:
    op.drop_table("engineering_handoffs")
