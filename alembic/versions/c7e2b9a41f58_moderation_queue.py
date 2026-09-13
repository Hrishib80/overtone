"""moderation queue: reviewers, suspension, and what was reported

Revision ID: c7e2b9a41f58
Revises: b1f4a7c92d30
Create Date: 2026-09-13

`users.status` is a validated string rather than a native enum, so
`suspended` needs no schema change — see the note in CLAUDE.md about why
lifecycle vocabularies that can still grow are not database enums.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7e2b9a41f58"
down_revision: Union[str, Sequence[str], None] = "b1f4a7c92d30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        # A server_default so the column can be NOT NULL on a table that
        # already has rows, dropped again immediately so the schema matches
        # the model — which declares a Python-side default and no server one.
        # Leaving it in place makes `alembic check` report drift forever.
        batch_op.add_column(
            sa.Column("is_reviewer", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.alter_column("is_reviewer", server_default=None)

    with op.batch_alter_table("reports", schema=None) as batch_op:
        batch_op.add_column(sa.Column("subject_media_id", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("subject_prompt_id", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("reviewer_id", sa.String(), nullable=True))
        batch_op.create_foreign_key(
            "fk_reports_subject_media", "media_assets", ["subject_media_id"], ["id"], ondelete="SET NULL"
        )
        batch_op.create_foreign_key(
            "fk_reports_subject_prompt",
            "prompt_responses",
            ["subject_prompt_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_reports_reviewer", "users", ["reviewer_id"], ["id"], ondelete="SET NULL"
        )


def downgrade() -> None:
    with op.batch_alter_table("reports", schema=None) as batch_op:
        batch_op.drop_constraint("fk_reports_reviewer", type_="foreignkey")
        batch_op.drop_constraint("fk_reports_subject_prompt", type_="foreignkey")
        batch_op.drop_constraint("fk_reports_subject_media", type_="foreignkey")
        batch_op.drop_column("reviewer_id")
        batch_op.drop_column("subject_prompt_id")
        batch_op.drop_column("subject_media_id")

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("is_reviewer")
