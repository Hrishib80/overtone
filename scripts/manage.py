"""Operational commands.

    python scripts/manage.py seed
    python scripts/manage.py scope-create --slug iitm --name "IIT Madras" \
        --domain smail.iitm.ac.in --domain iitm.ac.in --cap man=500 --cap woman=500
    python scripts/manage.py scope-list
    python scripts/manage.py waitlist-sweep

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
    Scope,
    ScopeKind,
    ScopeStatus,
    SegmentCap,
    User,
    UserStatus,
    WaitlistEntry,
    WaitlistStatus,
)
from backend.options import SEGMENTS  # noqa: E402
from backend.seeds import seed_all  # noqa: E402


async def cmd_seed(_args: argparse.Namespace) -> int:
    async with AsyncSessionLocal() as db:
        result = await seed_all(db)
    for table, (created, updated) in result.items():
        print(f"  {table:<20} {created} created, {updated} updated")
    return 0


async def cmd_scope_create(args: argparse.Namespace) -> int:
    caps: dict[str, int] = {}
    for item in args.cap or []:
        segment, _, value = item.partition("=")
        if segment not in SEGMENTS:
            print(f"Unknown segment '{segment}'. Expected one of: {', '.join(SEGMENTS)}", file=sys.stderr)
            return 2
        caps[segment] = int(value)

    async with AsyncSessionLocal() as db:
        existing = (await db.execute(select(Scope).where(Scope.slug == args.slug))).scalars().first()
        if existing is not None:
            print(f"Scope '{args.slug}' already exists.", file=sys.stderr)
            return 1

        scope = Scope(
            slug=args.slug,
            name=args.name,
            kind=ScopeKind(args.kind),
            email_domains=[d.lower().lstrip("@") for d in args.domain],
            status=ScopeStatus(args.status),
        )
        db.add(scope)
        await db.flush()

        for segment, cap in caps.items():
            db.add(SegmentCap(scope_id=scope.id, segment=segment, cap=cap))
        await db.commit()

    print(f"Created {args.kind} '{args.name}' ({args.slug})")
    print(f"  domains: {', '.join(args.domain)}")
    print(f"  caps   : {caps or 'uncapped'}")
    if caps:
        low = min(caps.values())
        if low < 500:
            print(
                f"\n  Note: the smallest cap is {low}. Similarity pairing needs roughly 500 per\n"
                "  segment to reach its design operating point, and about 100 to leave the\n"
                "  random bootstrap stage. Below that the mechanic still runs, but pairs are\n"
                "  drawn from too small a pool to be meaningful."
            )
    return 0


async def cmd_scope_list(_args: argparse.Namespace) -> int:
    async with AsyncSessionLocal() as db:
        scopes = (await db.execute(select(Scope).order_by(Scope.created_at))).scalars().all()
        if not scopes:
            print("No scopes yet. Create one with: manage.py scope-create")
            return 0

        for scope in scopes:
            print(f"\n{scope.name}  ({scope.slug}, {scope.kind}, {scope.status})")
            print(f"  domains: {', '.join(scope.email_domains or []) or '(none)'}")
            caps = {
                row.segment: row.cap
                for row in (await db.execute(select(SegmentCap).where(SegmentCap.scope_id == scope.id)))
                .scalars()
                .all()
            }
            for segment in SEGMENTS:
                occupied = (
                    await db.execute(
                        select(func.count(User.id))
                        .where(User.scope_id == scope.id)
                        .where(User.cap_segment == segment)
                        .where(User.status == UserStatus.active)
                        .where(User.deleted_at.is_(None))
                    )
                ).scalar_one()
                waiting = (
                    await db.execute(
                        select(func.count(WaitlistEntry.id))
                        .where(WaitlistEntry.scope_id == scope.id)
                        .where(WaitlistEntry.segment == segment)
                        .where(WaitlistEntry.status == WaitlistStatus.waiting)
                    )
                ).scalar_one()
                limit = caps.get(segment)
                shown = limit if limit is not None else "uncapped"
                print(f"  {segment:<10} {occupied}/{shown} active   {waiting} waiting")
    return 0


async def cmd_waitlist_sweep(_args: argparse.Namespace) -> int:
    """Expire unclaimed invitations and offer the freed slots to the next in line."""
    from backend import access

    async with AsyncSessionLocal() as db:
        expired = await access.expire_stale_invitations(db)
        await db.commit()

        invited = 0
        scopes = (await db.execute(select(Scope))).scalars().all()
        for scope in scopes:
            for segment in SEGMENTS:
                while await access.invite_next(db, scope.id, segment) is not None:
                    invited += 1
        await db.commit()

    print(f"Expired {expired} stale invitation(s); invited {invited} person(s).")
    return 0


COMMANDS = {
    "seed": cmd_seed,
    "scope-create": cmd_scope_create,
    "scope-list": cmd_scope_list,
    "waitlist-sweep": cmd_waitlist_sweep,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("seed", help="load reference data (idempotent)")

    create = sub.add_parser("scope-create", help="create a campus or city")
    create.add_argument("--slug", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--kind", default="campus", choices=[k.value for k in ScopeKind])
    create.add_argument("--status", default="building", choices=[s.value for s in ScopeStatus])
    create.add_argument("--domain", action="append", required=True, help="repeatable")
    create.add_argument("--cap", action="append", help="segment=number, repeatable")

    sub.add_parser("scope-list", help="show scopes with occupancy and waitlists")
    sub.add_parser("waitlist-sweep", help="expire stale invitations and invite the next in line")

    return parser


def main() -> int:
    args = build_parser().parse_args()
    if AsyncSessionLocal is None:
        print("DATABASE_URL is not configured.", file=sys.stderr)
        return 2
    return asyncio.run(COMMANDS[args.command](args))


if __name__ == "__main__":
    raise SystemExit(main())
