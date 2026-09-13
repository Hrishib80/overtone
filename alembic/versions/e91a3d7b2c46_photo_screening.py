"""photo screening: the held state and what a human said about it

Revision ID: e91a3d7b2c46
Revises: c7e2b9a41f58
Create Date: 2026-09-13

`media_assets.status` is a validated string rather than a native enum, so
`held` needs no schema change — only the two columns that record what the
machine saw and whether a person has since cleared it.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e91a3d7b2c46"
down_revision: Union[str, Sequence[str], None] = "c7e2b9a41f58"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("media_assets", schema=None) as batch_op:
        batch_op.add_column(sa.Column("screen_detail", sa.String(), nullable=True))
        batch_op.add_column(
            sa.Column("review_approved", sa.Boolean(), nullable=False, server_default=sa.false())
        )

    # Separate block, or SQLite rebuilds the table with the default already
    # gone and the row copy fails on the NOT NULL. See CLAUDE.md.
    with op.batch_alter_table("media_assets", schema=None) as batch_op:
        batch_op.alter_column("review_approved", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("media_assets", schema=None) as batch_op:
        batch_op.drop_column("review_approved")
        batch_op.drop_column("screen_detail")
