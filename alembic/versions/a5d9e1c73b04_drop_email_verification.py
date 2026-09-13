"""drop email verification

Revision ID: a5d9e1c73b04
Revises: f3b8c1e05a97
Create Date: 2026-09-13

There is no channel to verify on any more — no SMTP, no Google — so the step
is gone and nobody should be left waiting for it. Anyone still sitting in
`pending_verification` is moved to `onboarding`, which is where they would
have gone the moment they clicked a link that will now never arrive.

The `email_verifications` table and `users.email_verified_at` are **kept**.
The rows are the record of who did verify while it existed, and the column is
the seam to switch it back on: nothing sets it now, and the gate that read it
is gone from `profile.submit`. Dropping them would make re-enabling it a
migration with data loss rather than a migration.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a5d9e1c73b04"
down_revision: Union[str, Sequence[str], None] = "f3b8c1e05a97"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text("UPDATE users SET status = 'onboarding' WHERE status = 'pending_verification'")
    )


def downgrade() -> None:
    # Not reversible in any meaningful sense: which of these accounts had
    # verified before is no longer distinguishable from which had not, and
    # guessing would either lock out real people or admit unverified ones.
    pass
