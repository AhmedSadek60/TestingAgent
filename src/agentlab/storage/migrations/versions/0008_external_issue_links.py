"""external issue links: where a submission was published to, so a retry cannot publish it twice

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-13 10:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "external_issue_links",
        sa.Column("submission_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("external_number", sa.Integer(), nullable=True),
        sa.Column("url", sa.String(length=300), nullable=False),
        sa.Column("external_state", sa.String(length=10), nullable=False),
        sa.Column("error_kind", sa.String(length=20), nullable=False),
        sa.Column("error_detail", sa.String(length=300), nullable=False),
        sa.Column("submission_version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("repository", sa.String(length=200), nullable=False),
        sa.Column("published_by_label", sa.String(length=80), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_status", sa.String(length=40), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("submission_id", "provider", name="uq_external_issue_link"),
    )
    op.create_index("ix_external_issue_links_submission_id", "external_issue_links", ["submission_id"])


def downgrade() -> None:
    op.drop_table("external_issue_links")
