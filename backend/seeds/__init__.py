"""Seed loading for reference data.

Reference rows are data, not migrations: they change on their own cadence and
must be re-runnable. `seed_all` upserts, so running it twice is harmless and
running it after editing a JSON file updates what changed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import GenderIdentity, PromptLibrary, Sexuality
from backend.logging_config import get_logger

log = get_logger(__name__)

SEED_DIR = Path(__file__).parent


def load(name: str) -> dict[str, Any]:
    return json.loads((SEED_DIR / name).read_text(encoding="utf-8"))


async def _upsert(db: AsyncSession, model, rows: list[dict[str, Any]]) -> tuple[int, int]:
    """Insert new rows, update changed ones. Returns (created, updated)."""
    existing = {row.id: row for row in (await db.execute(select(model))).scalars().all()}
    created = updated = 0

    for row in rows:
        current = existing.get(row["id"])
        if current is None:
            db.add(model(**row))
            created += 1
            continue
        if any(getattr(current, key) != value for key, value in row.items()):
            for key, value in row.items():
                setattr(current, key, value)
            updated += 1

    return created, updated


async def seed_identity(db: AsyncSession) -> dict[str, tuple[int, int]]:
    data = load("identity.json")

    genders = [
        {
            "id": item["id"],
            "label": item["label"],
            "default_visible_as": item["default_visible_as"],
            "display_order": index,
            "is_active": True,
        }
        for index, item in enumerate(data["gender_identities"])
    ]
    sexualities = [
        {"id": item["id"], "label": item["label"], "display_order": index, "is_active": True}
        for index, item in enumerate(data["sexualities"])
    ]

    return {
        "gender_identities": await _upsert(db, GenderIdentity, genders),
        "sexualities": await _upsert(db, Sexuality, sexualities),
    }


async def seed_prompts(db: AsyncSession) -> dict[str, tuple[int, int]]:
    data = load("prompts.json")
    order = {c["slug"]: c["order"] for c in data["categories"]}

    rows = [
        {
            "id": item["id"],
            "text": item["text"],
            "category": item["category"],
            "emphasis": item.get("emphasis"),
            # Sort by category first, then by position within the file, so the
            # picker reads in the order the categories are meant to appear.
            "display_order": order.get(item["category"], 99) * 1000 + index,
            "is_active": True,
        }
        for index, item in enumerate(data["prompts"])
    ]
    return {"prompts": await _upsert(db, PromptLibrary, rows)}


async def seed_all(db: AsyncSession) -> dict[str, tuple[int, int]]:
    result = {**await seed_identity(db), **await seed_prompts(db)}
    await db.commit()
    for name, (created, updated) in result.items():
        log.info("seeded", table=name, created=created, updated=updated)
    return result
