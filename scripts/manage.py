"""Operational commands.

    python scripts/manage.py seed
    python scripts/manage.py stats
    python scripts/manage.py reviewer --username someone
    python scripts/manage.py reviewer --username someone --revoke

`seed` is idempotent — it upserts reference rows, so re-running after editing a
seed file applies only what changed.
"""

from __future__ import annotations

import argparse
import asyncio
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


COMMANDS = {
    "seed": cmd_seed,
    "stats": cmd_stats,
    "reviewer": cmd_reviewer,
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

    return parser


def main() -> int:
    args = build_parser().parse_args()
    if AsyncSessionLocal is None:
        print("DATABASE_URL is not configured.", file=sys.stderr)
        return 2
    return asyncio.run(COMMANDS[args.command](args))


if __name__ == "__main__":
    raise SystemExit(main())
