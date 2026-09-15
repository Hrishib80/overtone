"""waitlist, admins and invite codes

Revision ID: c4f7a2d91e35
Revises: b6c2e8f14a90
Create Date: 2026-09-15

A finished profile now waits for an admin before it is shown to anyone, unless
it arrived on an invite code from a member an admin approved. This adds the
columns for that; the status value `waitlisted` needs no DDL, because status is
a plain string column.

Every account that is already active stays active. They were members before
approval existed, and nothing about this change should take anybody off the
platform they are already on.

`is_admin` is added NOT NULL, which on SQLite needs the two-step dance recorded
in CLAUDE.md: a `server_default` so existing rows get a value while batch mode
rebuilds the table, then a second batch to drop the default again so `alembic
check` sees the model's Python-side default and nothing else. Doing both in one
batch fails on the first table with a row in it.

The two self-references are named explicitly. Autogenerate emits them unnamed,
which SQLite accepts on the way up and cannot drop on the way down.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4f7a2d91e35"
down_revision: str | Sequence[str] | None = "b6c2e8f14a90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("application_note", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("approved_by_id", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("invite_code", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("invited_by_id", sa.String(), nullable=True))
        batch_op.create_index(batch_op.f("ix_users_invite_code"), ["invite_code"], unique=True)
        batch_op.create_foreign_key(
            "fk_users_invited_by_id_users", "users", ["invited_by_id"], ["id"], ondelete="SET NULL"
        )
        batch_op.create_foreign_key(
            "fk_users_approved_by_id_users", "users", ["approved_by_id"], ["id"], ondelete="SET NULL"
        )

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column("is_admin", existing_type=sa.Boolean(), server_default=None)


def downgrade() -> None:
    # Anybody waiting when this is rolled back has nowhere to wait any more.
    # They go back to onboarding with their profile intact, which is where a
    # finished-but-unapproved profile lived before the waitlist existed.
    op.execute(sa.text("UPDATE users SET status = 'onboarding' WHERE status = 'waitlisted'"))

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_constraint("fk_users_approved_by_id_users", type_="foreignkey")
        batch_op.drop_constraint("fk_users_invited_by_id_users", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_users_invite_code"))
        batch_op.drop_column("invited_by_id")
        batch_op.drop_column("invite_code")
        batch_op.drop_column("approved_by_id")
        batch_op.drop_column("approved_at")
        batch_op.drop_column("application_note")
        batch_op.drop_column("applied_at")
        batch_op.drop_column("is_admin")
