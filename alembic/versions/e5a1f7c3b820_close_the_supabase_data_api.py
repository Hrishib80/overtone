"""close the Supabase data API

Revision ID: e5a1f7c3b820
Revises: c4f7a2d91e35
Create Date: 2026-09-15

Supabase publishes every table in `public` over its REST API, to the `anon`
and `authenticated` roles, and the anon key that unlocks it is not a secret —
it is designed to ship in browsers. With row-level security off, anybody who
had it could read `users`, password hashes included. This app never uses that
API: the server connects as the table owner, which RLS does not apply to, and
the browser talks only to our own API. So the API is closed outright:

* RLS on for every table, with no policies, so the data API sees no rows;
* every grant to `anon` and `authenticated` revoked, so it cannot even try;
* the same revocation as a *default* privilege, so a table added by a later
  migration is closed from the moment it exists rather than when somebody
  remembers. RLS itself cannot be defaulted, which is why the grants matter.

Only on Postgres, and only where those roles exist: SQLite has none of this,
and a plain Postgres (CI) has no `anon` role to revoke from.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e5a1f7c3b820"
down_revision: str | Sequence[str] | None = "c4f7a2d91e35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        DO $$
        DECLARE t record;
        BEGIN
            FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
                EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t.tablename);
            END LOOP;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon')
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
                REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated;
                REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;
                ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated;
                ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # Deliberately does not re-grant anything to `anon`: reopening a table of
    # password hashes to a public key is not something a rollback should do
    # silently. RLS is switched back off so the schema matches the revision.
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        DO $$
        DECLARE t record;
        BEGIN
            FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
                EXECUTE format('ALTER TABLE public.%I DISABLE ROW LEVEL SECURITY', t.tablename);
            END LOOP;
        END $$;
        """
    )
