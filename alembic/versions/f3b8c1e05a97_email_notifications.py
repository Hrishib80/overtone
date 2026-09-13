"""email notifications: the one preference that has to exist

Revision ID: f3b8c1e05a97
Revises: e91a3d7b2c46
Create Date: 2026-09-13

On by default. The two emails this gates are "somebody wrote to you" and
"they replied" — the moments the product turns on — and an account that
silently never hears about either is an account that quietly stops working.
Turning it off is one click from inside the email itself.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3b8c1e05a97"
down_revision: Union[str, Sequence[str], None] = "e91a3d7b2c46"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Two blocks: the server_default gives existing rows a value, then it goes
    # again so the schema matches a model that declares only a Python-side
    # default. Dropping it in the same block fails on SQLite, which rebuilds
    # the table from the block's final state before copying the rows in.
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("email_notifications", sa.Boolean(), nullable=False, server_default=sa.true())
        )

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column("email_notifications", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("email_notifications")
