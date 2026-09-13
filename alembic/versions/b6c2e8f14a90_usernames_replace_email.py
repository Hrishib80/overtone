"""usernames replace email as the account's identity

Revision ID: b6c2e8f14a90
Revises: a5d9e1c73b04
Create Date: 2026-09-13

Nobody joins with an email any more: an account is a username and a password.
Every existing account needs a username before the column can be NOT NULL, so
this migration makes one for each from the part of its address before the `@`
— `ravi@demo.edu` becomes `ravi` — adding a number where two would collide.

Three steps, and the order is not negotiable on SQLite:

1. Add `username` as **nullable**, in its own batch. Batch mode rebuilds the
   table from the block's final state and copies rows in afterwards, so a NOT
   NULL column added here would fail on the first table that has a row in it —
   which is exactly the trap `b273e43` paid for with `is_reviewer`.
2. Backfill every row.
3. Tighten `username` to NOT NULL with its unique index, and relax `email` to
   nullable, in a second batch.

The derivation is copied here rather than imported from `backend.usernames`.
A migration has to keep producing the same result for as long as it exists,
and application code is allowed to change underneath it.

`email` is not cleared. The addresses stay on the accounts that had them, and
nothing reads them; removing them is a separate decision with its own
migration.
"""

import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b6c2e8f14a90"
down_revision: Union[str, Sequence[str], None] = "a5d9e1c73b04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MIN_LENGTH, MAX_LENGTH = 3, 20
RESERVED = {
    "admin", "administrator", "api", "help", "me", "mod", "moderator", "null",
    "official", "overtone", "root", "settings", "staff", "support", "system",
    "undefined",
}  # fmt: skip
_SHAPE = re.compile(r"^[a-z0-9][a-z0-9_.]*$")


def _valid(name: str) -> bool:
    return (
        MIN_LENGTH <= len(name) <= MAX_LENGTH
        and bool(_SHAPE.match(name))
        and not name.endswith(".")
        and ".." not in name
        and name not in RESERVED
    )


def _derive(seed: str, taken: set[str]) -> str:
    base = (seed or "").split("@", 1)[0].lower()
    base = re.sub(r"[^a-z0-9_.]", "_", base)
    base = re.sub(r"\.{2,}", ".", base).strip("._")
    if not base:
        base = "member"
    base = base[:MAX_LENGTH].rstrip(".")
    if len(base) < MIN_LENGTH:
        base = (base + "_member")[:MAX_LENGTH]
    if base in RESERVED:
        base = (base + "_1")[:MAX_LENGTH]

    candidate, n = base, 1
    while candidate in taken or not _valid(candidate):
        n += 1
        suffix = str(n)
        candidate = base[: MAX_LENGTH - len(suffix)].rstrip(".") + suffix
    return candidate


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("username", sa.String(), nullable=True))

    bind = op.get_bind()
    # Oldest first, so the account that has held an address longest keeps the
    # plain name and a later collision gets the number.
    rows = bind.execute(
        sa.text("SELECT id, email FROM users ORDER BY created_at, id")
    ).fetchall()
    taken: set[str] = set()
    for user_id, email in rows:
        name = _derive(email or "", taken)
        taken.add(name)
        bind.execute(
            sa.text("UPDATE users SET username = :name WHERE id = :id"),
            {"name": name, "id": user_id},
        )

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column("username", existing_type=sa.String(), nullable=False)
        batch_op.create_index(batch_op.f("ix_users_username"), ["username"], unique=True)
        batch_op.alter_column("email", existing_type=sa.String(), nullable=True)


def downgrade() -> None:
    # Only reversible for accounts that still have an address. One made after
    # this migration has none, and there is nothing honest to invent — so the
    # downgrade refuses rather than writing placeholder addresses that would
    # look like real ones.
    bind = op.get_bind()
    orphans = bind.execute(sa.text("SELECT COUNT(*) FROM users WHERE email IS NULL")).scalar()
    if orphans:
        raise RuntimeError(
            f"{orphans} account(s) have no email to fall back to; "
            "refusing to downgrade and lose their only identity."
        )

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_users_username"))
        batch_op.alter_column("email", existing_type=sa.String(), nullable=False)
        batch_op.drop_column("username")
