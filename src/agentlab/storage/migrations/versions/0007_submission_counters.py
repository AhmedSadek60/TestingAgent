"""submission counters: a number that was given is never given again

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-13 09:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "submission_counters",
        sa.Column("type", sa.String(length=10), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("last_seq", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("type", "year"),
    )
    # what has been given out so far, so that numbering carries on from the highest number that exists
    op.execute(
        "INSERT INTO submission_counters (type, year, last_seq) SELECT type, year, MAX(seq) FROM submissions GROUP BY type, year"
    )


def downgrade() -> None:
    op.drop_table("submission_counters")
