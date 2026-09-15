"""Operational commands.

    python scripts/manage.py seed
    python scripts/manage.py stats
    python scripts/manage.py reviewer --username someone
    python scripts/manage.py reviewer --username someone --revoke
    python scripts/manage.py admin --username someone
    python scripts/manage.py admin --username someone --revoke
    python scripts/manage.py staff --username someone < password.txt

`staff` makes an account that can reach the admin portal and nothing else. The
password is read from stdin (or asked for at a terminal), never taken as an
argument, so it does not land in shell history or a process listing.

`seed` is idempotent — it upserts reference rows, so re-running after editing a
seed file applies only what changed.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

# Allow `python scripts/manage.py` as well as `python -m scripts.manage`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from backend.database import (  # noqa: E402
    AsyncSessionLocal,
    User,
    UserStatus,
    UserVisibleAs,
)
from backend.options import SEGMENTS  # noqa: E402
from backend.seeds import seed_all  # noqa: E402


async def cmd_seed(_args: argparse.Namespace) -> int:
    async with AsyncSessionLocal() as db:
        result = await seed_all(db)
    for table, (created, updated) in result.items():
        print(f"  {table:<20} {created} created, {updated} updated")
    return 0


async def cmd_stats(_args: argparse.Namespace) -> int:
    """How big the pool is, per segment.

    The number that decides whether the pair loop can work at all, and the
    reason a headline total is the wrong thing to watch. A segment needs at
    least eight people before any unlock is reachable, and roughly five hundred
    before the similarity band reaches its design point — so a thousand members
    split badly is still a broken app for whoever is on the short side.
    """
    async with AsyncSessionLocal() as db:
        total = await db.scalar(select(func.count(User.id)).where(User.status == UserStatus.active))
        print(f"\nActive members: {total}")
        print("\n  Visible as        count")
        print("  " + "-" * 46)

        for segment in SEGMENTS:
            count = await db.scalar(
                select(func.count(User.id))
                .join(UserVisibleAs, UserVisibleAs.user_id == User.id)
                .where(User.status == UserStatus.active)
                .where(UserVisibleAs.segment == segment)
            )
            if count < 8:
                note = "  too small for an unlock"
            elif count < 500:
                note = "  below the pairing design point"
            else:
                note = ""
            print(f"  {segment:<16} {count:>5}{note}")
        print()
    return 0


async def cmd_reviewer(args: argparse.Namespace) -> int:
    """Grant or revoke the moderation queue.

    Only here, never over HTTP. The account that can suspend other people is
    exactly the one an attacker with a stolen session would want, so the way
    to become one requires a shell on the machine holding the database.
    """
    username = args.username.strip().lstrip("@").lower()
    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.username == username))).scalars().first()
        if user is None:
            print(f"No account called {username}.", file=sys.stderr)
            return 1

        user.is_reviewer = not args.revoke
        await db.commit()

    verb = "no longer a reviewer" if args.revoke else "is now a reviewer"
    print(f"  @{username} {verb}")
    return 0


async def cmd_admin(args: argparse.Namespace) -> int:
    """Grant or revoke the admin portal — the waitlist and who joins.

    Only here, never over HTTP, for the same reason as reviewers: the account
    that decides who gets onto the platform must not be grantable from any
    surface an attacker could already hold a session on.
    """
    username = args.username.strip().lstrip("@").lower()
    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.username == username))).scalars().first()
        if user is None:
            print(f"No account called {username}.", file=sys.stderr)
            return 1
        if not args.revoke and user.status != "active":
            # An admin who is not yet a member themselves would be deciding
            # who joins a platform they cannot see.
            print(
                f"@{username} is {user.status}, not active. Finish or approve that account first.",
                file=sys.stderr,
            )
            return 1

        user.is_admin = not args.revoke
        await db.commit()

    # ASCII only: this prints to a Windows console, where a dash arrives garbled.
    # The portal's path is deliberately not printed: it lives in the frontend
    # (src/services/paths.js, or VITE_ADMIN_PATH), and an Admin link appears
    # in the navbar for the account once it is granted.
    verb = "no longer an admin" if args.revoke else "is now an admin. Open it from the navbar Admin link"
    print(f"  @{username} {verb}")
    return 0


async def cmd_staff(args: argparse.Namespace) -> int:
    """Create (or reset) an account that runs the admin portal and nothing else.

    Not a member made into an admin: a `staff` account has no profile, is never
    shown to anybody, cannot invite, and the app sends it to the portal from
    every other page. Running this again for the same name resets the password,
    which is the only password reset there is.
    """
    from backend import usernames
    from backend.auth import hash_password

    username = usernames.normalise(args.username.lstrip("@"))
    problem = usernames.problem(username)
    # The reserved list is for members; a staff name is chosen by whoever
    # holds the command line, so only the shape rules apply.
    if problem and problem != usernames.problem("admin"):
        print(f"@{username}: {problem}", file=sys.stderr)
        return 1

    if sys.stdin.isatty():
        password = getpass.getpass("Password: ")
        if getpass.getpass("Again: ") != password:
            print("Those did not match.", file=sys.stderr)
            return 1
    else:
        password = sys.stdin.readline().rstrip("\r\n")
    if len(password) < 12:
        print("Use at least 12 characters for an account that decides who joins.", file=sys.stderr)
        return 1

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.username == username))).scalars().first()
        if user is not None and user.status != UserStatus.staff:
            print(
                f"@{username} is a member ({user.status}). Staff accounts are separate; pick another name.",
                file=sys.stderr,
            )
            return 1
        created = user is None
        if created:
            user = User(username=username, display_name="Admin", status=UserStatus.staff)
            db.add(user)
        user.password_hash = hash_password(password)
        user.is_admin = True
        await db.commit()

    print(f"  @{username} {'created' if created else 'password reset'}; signs in straight to the portal")
    return 0


COMMANDS = {
    "seed": cmd_seed,
    "stats": cmd_stats,
    "reviewer": cmd_reviewer,
    "admin": cmd_admin,
    "staff": cmd_staff,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("seed", help="load reference data (idempotent)")
    sub.add_parser("stats", help="pool size per segment")

    reviewer = sub.add_parser("reviewer", help="grant or revoke the moderation queue")
    reviewer.add_argument("--username", required=True, help="the account to change")
    reviewer.add_argument("--revoke", action="store_true", help="take it away instead")

    admin = sub.add_parser("admin", help="grant or revoke the admin portal (the waitlist)")
    admin.add_argument("--username", required=True, help="the account to change")
    admin.add_argument("--revoke", action="store_true", help="take it away instead")

    staff = sub.add_parser("staff", help="create an account that can only reach the admin portal")
    staff.add_argument("--username", required=True, help="the account to create or reset")

    return parser


def main() -> int:
    args = build_parser().parse_args()
    if AsyncSessionLocal is None:
        print("DATABASE_URL is not configured.", file=sys.stderr)
        return 2
    return asyncio.run(COMMANDS[args.command](args))


if __name__ == "__main__":
    raise SystemExit(main())
