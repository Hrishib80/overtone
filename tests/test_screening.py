"""Automatic photo screening, and the third outcome.

The policy is pure, so most of this is arithmetic on thresholds — which is
the point of keeping it out of the model. The rest checks the two things that
make the feature honest rather than decorative:

* a photo the machine is unsure about is **held**, not passed and not
  rejected, and nobody but its owner can see it while it waits;
* a detector that was supposed to run and did not holds too. The opposite is
  how moderation quietly stops working — the model errors, everything sails
  through, and nothing in the product looks different.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from backend import screening
from backend.database import MediaAsset, MediaStatus, User
from backend.ml.base import FaceResult, NudityResult
from tests.conftest import TEST_DOMAIN, onboard, register_and_verify, run_jobs, upload_media
from tests.test_privacy import _uid

CLEAN = FaceResult(ok=True, face_count=1, det_score=0.99, age=27.0)


def nudity(score: float, *, ran: bool = True, labels: list[str] | None = None) -> NudityResult:
    return NudityResult(ran=ran, score=score, labels=labels or ["FEMALE_BREAST_EXPOSED"])


# ---------------------------------------------------------------------------
# The policy
# ---------------------------------------------------------------------------


def test_a_clean_photo_passes():
    assert screening.decide(CLEAN, nudity(0.0, labels=[])).verdict == screening.Verdict.passed


def test_no_detector_at_all_is_not_a_reason_to_hold():
    """`None` is a fact about the deployment, not about the photo. Holding on
    it would mean every photo in development waits for a reviewer who does not
    exist."""
    assert screening.decide(CLEAN, None).verdict == screening.Verdict.passed


def test_a_detector_that_failed_holds():
    """The difference between "looked and found nothing" and "did not look" is
    the whole of this feature's credibility."""
    call = screening.decide(CLEAN, nudity(0.0, ran=False))
    assert call.verdict == screening.Verdict.held
    assert call.reason == "screening_failed"


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0.0, screening.Verdict.passed),
        (screening.HOLD_NUDITY - 0.01, screening.Verdict.passed),
        (screening.HOLD_NUDITY, screening.Verdict.held),
        (screening.REJECT_NUDITY - 0.01, screening.Verdict.held),
        (screening.REJECT_NUDITY, screening.Verdict.rejected),
        (1.0, screening.Verdict.rejected),
    ],
)
def test_the_nudity_thresholds(score, expected):
    assert screening.decide(CLEAN, nudity(score)).verdict == expected


def test_a_rejection_says_what_it_saw():
    call = screening.decide(CLEAN, nudity(0.9, labels=["MALE_GENITALIA_EXPOSED"]))
    assert call.verdict == screening.Verdict.rejected
    assert "genitalia" in call.detail


def test_a_young_looking_face_is_held_and_never_rejected():
    """The estimate is routinely out by years, and the two errors do not cost
    the same: a wrongly-rejected nineteen-year-old is told by a machine that
    they look like a child, with nowhere to argue."""
    young = FaceResult(ok=True, face_count=1, det_score=0.99, age=15.0)
    call = screening.decide(young, nudity(0.0, labels=[]))
    assert call.verdict == screening.Verdict.held
    assert call.reason == "possibly_underage"


def test_an_age_at_the_margin_still_passes():
    adult = FaceResult(ok=True, face_count=1, det_score=0.99, age=screening.HOLD_UNDER_AGE)
    assert screening.decide(adult, nudity(0.0, labels=[])).verdict == screening.Verdict.passed


def test_no_age_estimate_is_not_a_finding():
    """A model pack without a genderage head should not hold every photo."""
    ageless = FaceResult(ok=True, face_count=1, det_score=0.99, age=None)
    assert screening.decide(ageless, None).verdict == screening.Verdict.passed


def test_explicit_content_outranks_a_young_face():
    """Both true is one queue entry, and a reviewer needs the finding they
    have to act on first."""
    young = FaceResult(ok=True, face_count=1, det_score=0.99, age=15.0)
    call = screening.decide(young, nudity(0.9))
    assert call.verdict == screening.Verdict.rejected


# ---------------------------------------------------------------------------
# What a held photo does to the rest of the app
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def held(client, db_sessionmaker, fake_storage, monkeypatch):
    """One account whose photo the machine was unsure about."""
    from backend import handlers

    class Unsure:
        screens = True

        def screen(self, image_bytes):
            return nudity(0.5)

    monkeypatch.setattr(handlers.ml, "screener", lambda: Unsure())

    person = await onboard(
        client, f"unsure@{TEST_DOMAIN}", visible_as=["woman"], interested_in=["man"], store=fake_storage
    )
    await run_jobs(db_sessionmaker)
    return person


@pytest.mark.asyncio
async def test_an_unsure_photo_is_held(client, db_sessionmaker, held):
    async with db_sessionmaker() as db:
        from sqlalchemy import select

        photos = (
            (await db.execute(select(MediaAsset).where(MediaAsset.user_id == _uid(held)))).scalars().all()
        )
    photo = next(p for p in photos if p.kind == "photo")
    assert photo.status == MediaStatus.held
    assert photo.gate_reason == "possible_explicit_content"
    assert photo.screen_detail


@pytest.mark.asyncio
async def test_the_owner_still_sees_their_held_photo(client, held):
    """They uploaded it and it is in their grid. Blanking it would say the
    upload failed, which is a different and untrue thing."""
    body = (await client.get("/api/media", headers=held["headers"])).json()
    photo = next(m for m in body["media"] if m["kind"] == "photo")
    assert photo["status"] == "held"
    assert photo["url"]


@pytest.mark.asyncio
async def test_a_held_photo_keeps_them_out_of_pairs(client, db_sessionmaker, held, fake_storage):
    """No visible photo means nothing for the mechanic to work on, so the
    account simply is not a candidate while it waits."""
    from backend.pairing import _eligible_candidates

    viewer = await onboard(
        client, f"looker@{TEST_DOMAIN}", visible_as=["man"], interested_in=["woman"], store=fake_storage
    )
    await run_jobs(db_sessionmaker)

    async with db_sessionmaker() as db:
        me = await db.get(User, _uid(viewer))
        candidates = await _eligible_candidates(db, viewer=me, segment="woman", viewer_visible_as=["man"])
    assert _uid(held) not in {c.user_id for c in candidates}


@pytest.mark.asyncio
async def test_a_held_photo_reaches_the_queue_with_no_report_behind_it(client, db_sessionmaker, held):
    reviewer = await register_and_verify(client, f"mod2@{TEST_DOMAIN}")
    async with db_sessionmaker() as db:
        user = await db.get(User, _uid(reviewer))
        user.is_reviewer = True
        await db.commit()

    body = (await client.get("/api/moderation/queue", headers=reviewer["headers"])).json()
    entry = next(s for s in body["subjects"] if s["user"]["id"] == _uid(held))
    assert entry["open_reports"] == 0
    assert entry["held_photos"] == 1
    assert entry["held_reasons"] == ["possible_explicit_content"]


@pytest.mark.asyncio
async def test_approving_a_held_photo_puts_it_back_in_play(client, db_sessionmaker, held, monkeypatch):
    from sqlalchemy import select

    reviewer = await register_and_verify(client, f"mod3@{TEST_DOMAIN}")
    async with db_sessionmaker() as db:
        user = await db.get(User, _uid(reviewer))
        user.is_reviewer = True
        await db.commit()
        photo_id = (
            (
                await db.execute(
                    select(MediaAsset.id)
                    .where(MediaAsset.user_id == _uid(held))
                    .where(MediaAsset.status == MediaStatus.held)
                )
            )
            .scalars()
            .first()
        )

    approved = await client.post(
        f"/api/moderation/subjects/{_uid(held)}/decide",
        headers=reviewer["headers"],
        json={"action": "approve_photo", "note": "Swimwear. Fine.", "media_id": photo_id},
    )
    assert approved.status_code == 200, approved.text

    # The worker finishes the job — electing a primary and embedding — because
    # that is where the model lives.
    await run_jobs(db_sessionmaker)

    async with db_sessionmaker() as db:
        photo = await db.get(MediaAsset, photo_id)
    assert photo.status == MediaStatus.processed
    assert photo.review_approved is True
    assert photo.is_primary is True


@pytest.mark.asyncio
async def test_a_human_decision_survives_the_next_automatic_pass(client, db_sessionmaker, held):
    """Otherwise the queue is a thing reviewers do twice: the screener would
    put the same photo straight back the next time the job ran."""
    from sqlalchemy import select

    reviewer = await register_and_verify(client, f"mod4@{TEST_DOMAIN}")
    async with db_sessionmaker() as db:
        user = await db.get(User, _uid(reviewer))
        user.is_reviewer = True
        await db.commit()
        photo_id = (
            (
                await db.execute(
                    select(MediaAsset.id)
                    .where(MediaAsset.user_id == _uid(held))
                    .where(MediaAsset.status == MediaStatus.held)
                )
            )
            .scalars()
            .first()
        )

    await client.post(
        f"/api/moderation/subjects/{_uid(held)}/decide",
        headers=reviewer["headers"],
        json={"action": "approve_photo", "note": "Fine.", "media_id": photo_id},
    )
    await run_jobs(db_sessionmaker)

    # Run the whole job again, with the same unsure screener still in place.
    from backend import handlers

    async with db_sessionmaker() as db:
        await handlers.process_photo(db, {"asset_id": photo_id})
        photo = await db.get(MediaAsset, photo_id)
    assert photo.status == MediaStatus.processed


@pytest.mark.asyncio
async def test_approving_something_that_was_not_held_is_refused(client, db_sessionmaker, held):
    from sqlalchemy import select

    reviewer = await register_and_verify(client, f"mod5@{TEST_DOMAIN}")
    async with db_sessionmaker() as db:
        user = await db.get(User, _uid(reviewer))
        user.is_reviewer = True
        await db.commit()
        voice_id = (
            (
                await db.execute(
                    select(MediaAsset.id)
                    .where(MediaAsset.user_id == _uid(held))
                    .where(MediaAsset.kind == "voice")
                )
            )
            .scalars()
            .first()
        )

    refused = await client.post(
        f"/api/moderation/subjects/{_uid(held)}/decide",
        headers=reviewer["headers"],
        json={"action": "approve_photo", "note": "n/a", "media_id": voice_id},
    )
    assert refused.status_code == 404


@pytest.mark.asyncio
async def test_screening_is_off_without_real_models(client, verified, fake_storage, db_sessionmaker):
    """The default path, and the one every other test in the suite runs on:
    no detector, so nothing is held and nothing is silently approved either."""
    from backend import ml

    ml.screener.cache_clear()
    assert ml.screener().screens is False

    asset_id = await upload_media(client, verified["headers"], fake_storage)
    await run_jobs(db_sessionmaker)
    async with db_sessionmaker() as db:
        photo = await db.get(MediaAsset, asset_id)
    assert photo.status == MediaStatus.processed
