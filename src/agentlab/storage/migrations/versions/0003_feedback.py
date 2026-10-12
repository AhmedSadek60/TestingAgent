"""feedback: submissions (feature requests and issue reports), their discussion, history, labels, relations and audit trail

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-12 18:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
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
        "submissions",
        sa.Column("human_id", sa.String(length=24), nullable=False),
        sa.Column("type", sa.String(length=10), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("requested_priority", sa.String(length=24), nullable=True),
        sa.Column("assigned_priority", sa.String(length=24), nullable=True),
        sa.Column("severity", sa.String(length=24), nullable=True),
        sa.Column("visibility", sa.String(length=12), nullable=False),
        sa.Column("reporter_user_id", sa.String(length=36), nullable=True),
        sa.Column("reporter_label", sa.String(length=80), nullable=False),
        sa.Column("assignee_user_id", sa.String(length=36), nullable=True),
        sa.Column("project_id", sa.String(length=64), nullable=True),
        sa.Column("related_run_id", sa.String(length=64), nullable=True),
        sa.Column("related_test_id", sa.String(length=64), nullable=True),
        sa.Column("related_trace_id", sa.String(length=64), nullable=True),
        sa.Column("related_report_id", sa.String(length=64), nullable=True),
        sa.Column("related_target_id", sa.String(length=64), nullable=True),
        sa.Column("app_version", sa.String(length=40), nullable=True),
        sa.Column("build", sa.String(length=80), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_staff_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reporter_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        *_entity(),
        sa.ForeignKeyConstraint(["reporter_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["assignee_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("type", "year", "seq", name="uq_submission_number"),
    )
    for column, unique in (
        ("human_id", True),
        ("type", False),
        ("category", False),
        ("assigned_priority", False),
        ("severity", False),
        ("visibility", False),
        ("reporter_user_id", False),
        ("assignee_user_id", False),
        ("project_id", False),
        ("related_run_id", False),
        ("last_activity_at", False),
    ):
        op.create_index(f"ix_submissions_{column}", "submissions", [column], unique=unique)

    op.create_table(
        "feature_request_details",
        sa.Column("submission_id", sa.String(length=36), nullable=False),
        sa.Column("problem_statement", sa.Text(), nullable=False),
        sa.Column("expected_behavior", sa.Text(), nullable=False),
        sa.Column("primary_use_case", sa.Text(), nullable=False),
        sa.Column("why_important", sa.Text(), nullable=False),
        sa.Column("users_affected", sa.Text(), nullable=False),
        sa.Column("business_impact", sa.Text(), nullable=False),
        sa.Column("use_frequency", sa.String(length=120), nullable=False),
        sa.Column("current_workaround", sa.Text(), nullable=False),
        sa.Column("proposed_solution", sa.Text(), nullable=False),
        sa.Column("acceptance_criteria", sa.Text(), nullable=False),
        sa.Column("related_area", sa.String(length=200), nullable=False),
        sa.Column("dependencies", sa.Text(), nullable=False),
        *_entity(),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_feature_request_details_submission_id", "feature_request_details", ["submission_id"], unique=True
    )

    op.create_table(
        "issue_report_details",
        sa.Column("submission_id", sa.String(length=36), nullable=False),
        sa.Column("impact_summary", sa.Text(), nullable=False),
        sa.Column("expected_behavior", sa.Text(), nullable=False),
        sa.Column("actual_behavior", sa.Text(), nullable=False),
        sa.Column("reproduction_steps", sa.Text(), nullable=False),
        sa.Column("not_reproducible", sa.Boolean(), nullable=False),
        sa.Column("frequency", sa.String(length=20), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consistent", sa.Boolean(), nullable=True),
        sa.Column("blocks_work", sa.Boolean(), nullable=True),
        sa.Column("workaround", sa.Text(), nullable=False),
        sa.Column("reported_impact", sa.String(length=30), nullable=False),
        sa.Column("ui_screen", sa.String(length=200), nullable=False),
        sa.Column("worker_job_id", sa.String(length=64), nullable=True),
        sa.Column("provider_config_id", sa.String(length=64), nullable=True),
        *_entity(),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_issue_report_details_submission_id", "issue_report_details", ["submission_id"], unique=True)
    op.create_index("ix_issue_report_details_frequency", "issue_report_details", ["frequency"])
    op.create_index("ix_issue_report_details_reported_impact", "issue_report_details", ["reported_impact"])

    op.create_table(
        "submission_comments",
        sa.Column("submission_id", sa.String(length=36), nullable=False),
        sa.Column("author_user_id", sa.String(length=36), nullable=True),
        sa.Column("author_label", sa.String(length=80), nullable=False),
        sa.Column("author_is_staff", sa.Boolean(), nullable=False),
        sa.Column("visibility", sa.String(length=10), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        *_entity(),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["author_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_submission_comments_submission_id", "submission_comments", ["submission_id"])
    op.create_index("ix_submission_comments_visibility", "submission_comments", ["visibility"])

    op.create_table(
        "submission_status_history",
        sa.Column("submission_id", sa.String(length=36), nullable=False),
        sa.Column("old_status", sa.String(length=40), nullable=True),
        sa.Column("new_status", sa.String(length=40), nullable=False),
        sa.Column("changed_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("changed_by_label", sa.String(length=80), nullable=False),
        sa.Column("by_staff", sa.Boolean(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        *_entity(),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_submission_status_history_submission_id", "submission_status_history", ["submission_id"])

    op.create_table(
        "submission_labels",
        sa.Column("submission_id", sa.String(length=36), nullable=False),
        sa.Column("label", sa.String(length=60), nullable=False),
        *_entity(),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("submission_id", "label", name="uq_submission_label"),
    )
    op.create_index("ix_submission_labels_submission_id", "submission_labels", ["submission_id"])
    op.create_index("ix_submission_labels_label", "submission_labels", ["label"])

    op.create_table(
        "submission_relations",
        sa.Column("from_id", sa.String(length=36), nullable=False),
        sa.Column("to_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("created_by_label", sa.String(length=80), nullable=False),
        *_entity(),
        sa.ForeignKeyConstraint(["from_id"], ["submissions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["to_id"], ["submissions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("from_id", "to_id", "kind", name="uq_submission_relation"),
    )
    op.create_index("ix_submission_relations_from_id", "submission_relations", ["from_id"])
    op.create_index("ix_submission_relations_to_id", "submission_relations", ["to_id"])

    op.create_table(
        "submission_audit_events",
        sa.Column("submission_id", sa.String(length=36), nullable=True),
        sa.Column("event", sa.String(length=40), nullable=False),
        sa.Column("actor_label", sa.String(length=80), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=True),
        sa.Column("client", sa.String(length=64), nullable=False),
        sa.Column("detail", JSON, nullable=False),
        *_entity(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_submission_audit_events_submission_id", "submission_audit_events", ["submission_id"])
    op.create_index("ix_submission_audit_events_event", "submission_audit_events", ["event"])


def downgrade() -> None:
    for name in (
        "submission_audit_events",
        "submission_relations",
        "submission_labels",
        "submission_status_history",
        "submission_comments",
        "issue_report_details",
        "feature_request_details",
        "submissions",
    ):
        op.drop_table(name)  # their indexes go with them
