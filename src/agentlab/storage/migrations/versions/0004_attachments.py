"""attachments: the files attached to a submission

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-12 21:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "submission_attachments",
        sa.Column("submission_id", sa.String(length=36), nullable=False),
        sa.Column("uploader_user_id", sa.String(length=36), nullable=True),
        sa.Column("uploader_label", sa.String(length=80), nullable=False),
        sa.Column("uploader_is_staff", sa.Boolean(), nullable=False),
        sa.Column("visibility", sa.String(length=10), nullable=False),
        sa.Column("original_name", sa.String(length=200), nullable=False),
        sa.Column("extension", sa.String(length=16), nullable=False),
        sa.Column("declared_type", sa.String(length=120), nullable=False),
        sa.Column("detected_type", sa.String(length=120), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("artifact_id", sa.String(length=80), nullable=False),
        sa.Column("sanitized_artifact_id", sa.String(length=80), nullable=True),
        sa.Column("text_like", sa.Boolean(), nullable=False),
        sa.Column("secrets_status", sa.String(length=16), nullable=False),
        sa.Column("secrets_report", JSON, nullable=False),
        sa.Column("scan_status", sa.String(length=16), nullable=False),
        sa.Column("scan_detail", sa.String(length=200), nullable=False),
        sa.Column("archive_summary", JSON, nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("removed_by_label", sa.String(length=80), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploader_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_submission_attachments_submission_id", "submission_attachments", ["submission_id"])
    op.create_index("ix_submission_attachments_sha256", "submission_attachments", ["sha256"])


def downgrade() -> None:
    op.drop_table("submission_attachments")
