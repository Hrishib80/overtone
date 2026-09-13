"""Populate a local database with enough people to exercise the pair loop.

Development only — it writes users directly rather than walking the signup
flow, and it fabricates face embeddings instead of running the real models.
Never point this at anything but a local database.

    python scripts/seed_demo.py

The pool is sized so the unlock is reachable *in both directions*. Clearing
the Wilson bound takes seven comparisons of one person and a pair may only be
shown once, so a viewer needs at least eight candidates before any unlock is
possible. Ten and ten: a lopsided pool silently makes the whole mechanic
unreachable for whoever is on the short side of it.

Portraits are generated as SVG files under MEDIA_ROOT and served by the app's
own /media_uploads mount, so the demo needs no network and no object storage.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import random
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from backend.config import settings  # noqa: E402
from backend.database import (  # noqa: E402
    AsyncSessionLocal,
    MediaAsset,
    MediaKind,
    MediaStatus,
    Profile,
    ProfileEmbedding,
    PromptKind,
    PromptResponse,
    User,
    UserInterestedIn,
    UserStatus,
    UserVisibleAs,
    generate_uuid,
    utcnow,
)
from backend.ml.base import FACE_DIM, TEXT_DIM, VOICE_DIM, l2_normalise  # noqa: E402

PASSWORD_HASH_FOR = "overtone2026"

PALETTE = [
    ("#b50000", "#fdeceb"),
    ("#003fb6", "#e9f0fc"),
    ("#0f6b52", "#e4f3ed"),
    ("#8a5300", "#fbf1e0"),
    ("#5b6e83", "#eef4fa"),
    ("#7fb8ea", "#0b1420"),
]

PEOPLE = [
    ("Aditi", "woman", ["man"], "Computer Science", "Listening properly, even when it's inconvenient."),
    ("Meera", "woman", ["man"], "Design", "Chai at 4pm with my phone face down."),
    ("Priya", "woman", ["man"], "Economics", "Arguing about films I haven't finished."),
    ("Kavya", "woman", ["man"], "Architecture", "Long walks that were supposed to be short."),
    ("Sneha", "woman", ["man"], "Literature", "Reading the last page first. I'm not sorry."),
    ("Divya", "woman", ["man"], "Physics", "Explaining things nobody asked about."),
    ("Anjali", "woman", ["man"], "Medicine", "Remembering what you said three weeks ago."),
    ("Nandini", "woman", ["man"], "History", "Finding the one good bench on campus."),
    ("Ishita", "woman", ["man"], "Chemistry", "Making tea for people who didn't ask."),
    ("Rhea", "woman", ["man"], "Mathematics", "Losing at carrom with dignity."),
    ("Ravi", "man", ["woman"], "Mechanical", "Fixing things that were working fine."),
    ("Arjun", "man", ["woman"], "Law", "Losing arguments on purpose to see what happens."),
    ("Karthik", "man", ["woman"], "Electrical", "Naming every stray dog on the road."),
    ("Vikram", "man", ["woman"], "Philosophy", "Being wrong out loud, quickly."),
    ("Rohit", "man", ["woman"], "Civil", "Reading plaques nobody else stops for."),
    ("Aman", "man", ["woman"], "Statistics", "Keeping a spreadsheet I will never show you."),
    ("Nikhil", "man", ["woman"], "Music", "Humming the wrong harmony, confidently."),
    ("Siddharth", "man", ["woman"], "Geology", "Picking up rocks and putting them in my bag."),
    ("Varun", "man", ["woman"], "Biology", "Talking to plants. They have not replied."),
    ("Dev", "man", ["woman"], "Linguistics", "Correcting nobody, silently, forever."),
]

PROMPTS = [
    ("greatest_strength", PromptKind.written),
    ("simple_pleasures", PromptKind.written),
    ("life_goal", PromptKind.written),
    ("how_to_pronounce_my_name", PromptKind.voice),
]


def portrait_svg(name: str, ink: str, ground: str) -> str:
    """A stand-in for a photo: a single initial on a coloured ground.

    Deliberately carries no name — round 1 withholds everything but the
    photograph, and a placeholder with a name printed on it would defeat the
    one rule the whole mechanic rests on.
    """
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 400">'
        f'<rect width="300" height="400" fill="{ground}"/>'
        f'<circle cx="150" cy="150" r="78" fill="{ink}" opacity="0.9"/>'
        f'<text x="150" y="178" text-anchor="middle" font-family="Inter,sans-serif" '
        f'font-size="82" font-weight="700" fill="{ground}">{name[0]}</text>'
        "</svg>"
    )


def face_vector(index: int, rng: random.Random) -> list[float]:
    """Clustered, not random: people near each other in this list look alike,
    so the similarity band has something real to bite on."""
    cluster = index // 3
    base = [math.sin(cluster * 1.7 + i * 0.11) for i in range(FACE_DIM)]
    jitter = [rng.gauss(0, 0.05) for _ in range(FACE_DIM)]
    return l2_normalise([b + j for b, j in zip(base, jitter, strict=True)])


async def seed(media_root: Path) -> None:
    rng = random.Random(7)
    media_root.mkdir(parents=True, exist_ok=True)

    from passlib.context import CryptContext

    pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
    password_hash = pwd.hash(PASSWORD_HASH_FOR)

    async with AsyncSessionLocal() as db:
        domain = "demo.edu"
        created = 0

        for index, (name, visible, interested, school, strength) in enumerate(PEOPLE):
            email = f"{name.lower()}@{domain}"
            if (await db.execute(select(User).where(User.email == email))).scalars().first():
                continue

            user = User(
                email=email,
                password_hash=password_hash,
                display_name=name,
                birthdate=date(2003, 1 + index % 12, 1 + index % 27),
                status=UserStatus.active,
                email_verified_at=utcnow(),
            )
            db.add(user)
            await db.flush()

            db.add(UserVisibleAs(user_id=user.id, segment=visible, is_primary=True))
            for segment in interested:
                db.add(UserInterestedIn(user_id=user.id, segment=segment))

            db.add(
                Profile(
                    user_id=user.id,
                    gender_identity_id=visible,
                    pronouns="she/her" if visible == "woman" else "he/him",
                    height_cm=158 + (index * 4) % 28,
                    location="Campus",
                    school=school,
                    dating_intentions="long_term",
                    relationship_type="monogamy",
                    religion="hindu",
                    drinking="sometimes",
                    smoking="no",
                    languages=["english", "hindi", "telugu"][: 1 + index % 3],
                )
            )

            ink, ground = PALETTE[index % len(PALETTE)]
            filename = f"demo-{user.id}.svg"
            (media_root / filename).write_text(portrait_svg(name, ink, ground), encoding="utf-8")

            db.add(
                MediaAsset(
                    user_id=user.id,
                    kind=MediaKind.photo,
                    object_key=filename,
                    public_url=f"/media_uploads/{filename}",
                    content_type="image/svg+xml",
                    byte_size=1024,
                    status=MediaStatus.processed,
                    is_primary=True,
                )
            )

            voice_asset_id = generate_uuid()
            db.add(
                MediaAsset(
                    id=voice_asset_id,
                    user_id=user.id,
                    kind=MediaKind.voice,
                    object_key=f"demo-voice-{user.id}.webm",
                    public_url=f"/media_uploads/demo-voice-{user.id}.webm",
                    content_type="audio/webm",
                    byte_size=2048,
                    # Left unprocessed on purpose: the reveal has to cope with
                    # a clip that is not playable yet, and this keeps that path
                    # exercised in the demo rather than only in tests.
                    status=MediaStatus.uploaded,
                )
            )

            bodies = [strength, "Small things, mostly.", "Learn to sail properly."]
            for slot, (prompt_id, kind) in enumerate(PROMPTS, start=1):
                db.add(
                    PromptResponse(
                        user_id=user.id,
                        prompt_id=prompt_id,
                        kind=kind,
                        slot=slot,
                        body=bodies[slot - 1] if kind == PromptKind.written else None,
                        audio_key=voice_asset_id if kind == PromptKind.voice else None,
                        audio_duration_ms=9000 if kind == PromptKind.voice else None,
                    )
                )

            db.add(
                ProfileEmbedding(
                    user_id=user.id,
                    face_vector=face_vector(index, rng),
                    voice_vector=l2_normalise([rng.gauss(0, 1) for _ in range(VOICE_DIM)]),
                    text_vector=l2_normalise([rng.gauss(0, 1) for _ in range(TEXT_DIM)]),
                )
            )
            created += 1

        await db.commit()

    print(f"Seeded {created} demo people.")
    print(f"Sign in as any of: {', '.join(n.lower() + '@' + domain for n, *_ in PEOPLE[:3])} …")
    print(f"Password for all: {PASSWORD_HASH_FOR}")


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()

    if settings.is_production:
        print("Refusing to run against a production environment.", file=sys.stderr)
        return 2
    if AsyncSessionLocal is None:
        print("DATABASE_URL is not configured.", file=sys.stderr)
        return 2

    asyncio.run(seed(Path(settings.media_root)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
