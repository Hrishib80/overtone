"""biometric consent

Revision ID: b1f4a7c92d30
Revises: d3c9cf01a102
Create Date: 2026-09-13

The first migration that is *layered* rather than squashed into the initial
one. Everything before this point could be regenerated freely because nothing
was deployed; there is now a database in use with real photos in it, and
rewriting the revision it is stamped with would leave `alembic upgrade head`
unable to find where it is. From here, migrations are added, never replaced.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1f4a7c92d30"
down_revision: Union[str, Sequence[str], None] = "d3c9cf01a102"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "biometric_consents",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("notice_version", sa.String(), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("biometric_consents", schema=None) as batch_op:
        batch_op.create_index("ix_biometric_consent_user", ["user_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("biometric_consents", schema=None) as batch_op:
        batch_op.drop_index("ix_biometric_consent_user")
    op.drop_table("biometric_consents")
