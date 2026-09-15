"""The review queue, and suspension.

The thing being tested is that reporting now leads somewhere. Everything else
here follows from two rules that are easy to get wrong in the other direction:

* A reviewer is made from the command line, so no request may create one, and
  a non-reviewer must not even learn the surface exists.
* Suspension removes somebody from other people's experience without removing
  their rights over their own data — they can still reach settings, withdraw
  consent, and delete themselves.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.database import MediaAsset, MediaStatus, Report, ReportStatus, User, UserStatus
from tests.conftest import all_route_paths, onboard, register_and_verify, run_jobs, upload_media


async def _make_reviewer(db_sessionmaker, user_id: str) -> None:
    """What `python scripts/manage.py reviewer --username …` does."""
    async with db_sessionmaker() as db:
        user = await db.get(User, user_id)
        user.is_reviewer = True
        await db.commit()


def _uid(account: dict) -> str:
    from backend.auth import verify_ws_token

    return verify_ws_token(account["access_token"])


@pytest_asyncio.fixture
async def reported(client, db_sessionmaker, fake_storage):
    """One account with two reports against it, and a reviewer to read them."""
    subject = await onboard(
        client, "trouble", visible_as=["man"], interested_in=["woman"], store=fake_storage
    )
    first = await onboard(client, "first", visible_as=["woman"], interested_in=["man"], store=fake_storage)
    second = await onboard(client, "second", visible_as=["woman"], interested_in=["man"], store=fake_storage)
    await run_jobs(db_sessionmaker)

    for reporter, reason in ((first, "harassment"), (second, "harassment")):
        filed = await client.post(
            "/api/safety/reports",
            headers=reporter["headers"],
            json={"user_id": _uid(subject), "reason": reason, "note": "said something awful"},
        )
        assert filed.status_code == 201, filed.text

    reviewer = await register_and_verify(client, "mod1")
    await _make_reviewer(db_sessionmaker, _uid(reviewer))
    return {"subject": subject, "reviewer": reviewer, "reporters": [first, second]}


# ---------------------------------------------------------------------------
# Who may look
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_ordinary_account_cannot_find_the_queue(client, reported):
    """404, not 403. That a moderation surface exists at this path is itself
    information, and not information anybody outside the team needs."""
    refused = await client.get("/api/moderation/queue", headers=reported["subject"]["headers"])
    assert refused.status_code == 404


@pytest.mark.asyncio
async def test_nothing_over_http_can_make_a_reviewer(client, reported):
    """The grant is a shell command on purpose. If this ever fails, some route
    has started writing `is_reviewer` and the whole model is gone."""
    import backend.moderation  # noqa: F401
    from backend.app import create_app

    app = create_app()
    paths = all_route_paths(app)
    # Proves the walk reached inside the routers. Without it, this assertion
    # passed with every route in the app invisible to it.
    assert "/api/moderation/queue" in paths
    assert not any("reviewer" in path for path in paths)

    # And the field is not writable through the profile editor either.
    refused = await client.patch(
        "/api/profile",
        headers=reported["subject"]["headers"],
        json={"is_reviewer": True},
    )
    assert refused.status_code in (400, 422)


@pytest.mark.asyncio
async def test_a_reviewer_sees_the_queue_heaviest_first(client, db_sessionmaker, reported, fake_storage):
    quiet = await onboard(client, "quiet", visible_as=["man"], interested_in=["woman"], store=fake_storage)
    reporter = reported["reporters"][0]
    await client.post(
        "/api/safety/reports",
        headers=reporter["headers"],
        json={"user_id": _uid(quiet), "reason": "fake"},
    )

    body = (await client.get("/api/moderation/queue", headers=reported["reviewer"]["headers"])).json()
    counts = [s["open_reports"] for s in body["subjects"]]
    assert counts == sorted(counts, reverse=True)
    assert counts[0] == 2


# ---------------------------------------------------------------------------
# What a reviewer sees
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_subject_view_carries_the_evidence(client, reported):
    subject_id = _uid(reported["subject"])
    body = (
        await client.get(f"/api/moderation/subjects/{subject_id}", headers=reported["reviewer"]["headers"])
    ).json()

    assert body["user"]["id"] == subject_id
    assert body["photos"], "a reviewer judging a photo has to be able to see it"
    assert body["prompts"], "and to read what they wrote"
    # The question, not its slug. A queue that shows `greatest_strength` above
    # an answer is a database view, not somebody's profile.
    assert all(p["text"] and p["text"] != p["prompt_id"] for p in body["prompts"])
    assert len(body["reports"]) == 2
    # Two different people, not one person twice — the distinction the whole
    # "four unrelated reports is the pattern" reasoning rests on.
    assert len({r["reporter"]["id"] for r in body["reports"]}) == 2


@pytest.mark.asyncio
async def test_a_report_can_name_one_photo(client, db_sessionmaker, reported, fake_storage):
    subject_id = _uid(reported["subject"])
    async with db_sessionmaker() as db:
        photo_id = (
            (await db.execute(select(MediaAsset.id).where(MediaAsset.user_id == subject_id)))
            .scalars()
            .first()
        )

    third = await onboard(client, "third", visible_as=["woman"], interested_in=["man"], store=fake_storage)
    filed = await client.post(
        "/api/safety/reports",
        headers=third["headers"],
        json={"user_id": subject_id, "reason": "sexual", "media_id": photo_id},
    )
    assert filed.status_code == 201

    body = (
        await client.get(f"/api/moderation/subjects/{subject_id}", headers=reported["reviewer"]["headers"])
    ).json()
    named = [r for r in body["reports"] if r["about_photo"]]
    assert len(named) == 1
    assert named[0]["about_photo"] == photo_id


@pytest.mark.asyncio
async def test_you_cannot_pin_a_report_to_somebody_elses_photo(client, db_sessionmaker, reported):
    """Otherwise a report is an accusation attached to the wrong evidence."""
    reporter = reported["reporters"][0]
    async with db_sessionmaker() as db:
        their_own_photo = (
            (await db.execute(select(MediaAsset.id).where(MediaAsset.user_id == _uid(reporter))))
            .scalars()
            .first()
        )

    refused = await client.post(
        "/api/safety/reports",
        headers=reporter["headers"],
        json={"user_id": _uid(reported["subject"]), "reason": "sexual", "media_id": their_own_photo},
    )
    assert refused.status_code == 404


# ---------------------------------------------------------------------------
# Deciding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_decision_closes_every_open_report(client, db_sessionmaker, reported):
    subject_id = _uid(reported["subject"])
    decided = await client.post(
        f"/api/moderation/subjects/{subject_id}/decide",
        headers=reported["reviewer"]["headers"],
        json={"action": "dismiss", "note": "Read the thread; ordinary disagreement."},
    )
    assert decided.status_code == 200
    assert decided.json()["reports_closed"] == 2

    async with db_sessionmaker() as db:
        rows = (await db.execute(select(Report).where(Report.subject_id == subject_id))).scalars().all()
    assert {r.status for r in rows} == {ReportStatus.dismissed}
    assert all(r.reviewer_id == _uid(reported["reviewer"]) for r in rows)
    assert all(r.reviewer_note for r in rows)


@pytest.mark.asyncio
async def test_a_decision_needs_a_note(client, reported):
    """A dismissal with no reason recorded is indistinguishable from nobody
    having looked, which is the exact thing this queue exists to stop."""
    refused = await client.post(
        f"/api/moderation/subjects/{_uid(reported['subject'])}/decide",
        headers=reported["reviewer"]["headers"],
        json={"action": "dismiss", "note": ""},
    )
    assert refused.status_code == 422


@pytest.mark.asyncio
async def test_removing_a_photo_hands_primary_on(client, db_sessionmaker, reported, fake_storage):
    subject = reported["subject"]
    subject_id = _uid(subject)
    await upload_media(client, subject["headers"], fake_storage)
    await run_jobs(db_sessionmaker)

    async with db_sessionmaker() as db:
        photos = (
            (
                await db.execute(
                    select(MediaAsset)
                    .where(MediaAsset.user_id == subject_id)
                    .where(MediaAsset.status == MediaStatus.processed)
                    .order_by(MediaAsset.display_order, MediaAsset.created_at)
                )
            )
            .scalars()
            .all()
        )
        assert len(photos) >= 2
        primary_id = next(p.id for p in photos if p.is_primary)

    removed = await client.post(
        f"/api/moderation/subjects/{subject_id}/decide",
        headers=reported["reviewer"]["headers"],
        json={"action": "remove_photo", "note": "Nudity.", "media_id": primary_id},
    )
    assert removed.status_code == 200, removed.text

    async with db_sessionmaker() as db:
        gone = await db.get(MediaAsset, primary_id)
        assert gone.status == MediaStatus.rejected
        assert gone.gate_reason == "removed_by_review"

        still = (
            (
                await db.execute(
                    select(MediaAsset)
                    .where(MediaAsset.user_id == subject_id)
                    .where(MediaAsset.status == MediaStatus.processed)
                )
            )
            .scalars()
            .all()
        )
    assert sum(1 for p in still if p.is_primary) == 1, "an account with photos must show one"


@pytest.mark.asyncio
async def test_removing_a_photo_that_is_not_theirs_is_refused(client, db_sessionmaker, reported):
    reporter = reported["reporters"][0]
    async with db_sessionmaker() as db:
        someone_elses = (
            (await db.execute(select(MediaAsset.id).where(MediaAsset.user_id == _uid(reporter))))
            .scalars()
            .first()
        )

    refused = await client.post(
        f"/api/moderation/subjects/{_uid(reported['subject'])}/decide",
        headers=reported["reviewer"]["headers"],
        json={"action": "remove_photo", "note": "Nudity.", "media_id": someone_elses},
    )
    assert refused.status_code == 404


# ---------------------------------------------------------------------------
# Suspension
# ---------------------------------------------------------------------------


async def _suspend(client, reported) -> None:
    done = await client.post(
        f"/api/moderation/subjects/{_uid(reported['subject'])}/decide",
        headers=reported["reviewer"]["headers"],
        json={"action": "suspend", "note": "Two unrelated harassment reports, both credible."},
    )
    assert done.status_code == 200, done.text


@pytest.mark.asyncio
async def test_a_suspended_account_cannot_reach_other_people(client, db_sessionmaker, reported):
    await _suspend(client, reported)
    headers = reported["subject"]["headers"]

    async with db_sessionmaker() as db:
        assert (await db.get(User, _uid(reported["subject"]))).status == UserStatus.suspended

    for method, path in (
        ("GET", "/api/pairs/next"),
        ("GET", "/api/connections"),
        ("GET", "/api/media"),
        ("GET", "/api/profile"),
        ("GET", "/api/safety/blocks"),
    ):
        response = await client.request(method, path, headers=headers)
        assert response.status_code == 403, f"{path} let a suspended account through"


@pytest.mark.asyncio
async def test_a_suspended_account_keeps_its_own_data_rights(client, reported):
    """Suspension is a judgement about how somebody treated other people. It
    is not a forfeit of what is theirs."""
    await _suspend(client, reported)
    headers = reported["subject"]["headers"]

    assert (await client.get("/api/auth/me", headers=headers)).status_code == 200
    assert (await client.get("/api/account/consent", headers=headers)).status_code == 200
    assert (await client.delete("/api/account/consent", headers=headers)).status_code == 200

    deleted = await client.post(
        "/api/account/delete", headers=headers, json={"password": "a-strong-enough-password"}
    )
    assert deleted.status_code == 200


@pytest.mark.asyncio
async def test_a_suspended_account_can_still_sign_in(client, reported):
    """Otherwise the settings they are still entitled to are unreachable the
    moment their token expires."""
    await _suspend(client, reported)

    signed_in = await client.post(
        "/api/auth/login",
        json={"username": "trouble", "password": "a-strong-enough-password"},
    )
    assert signed_in.status_code == 200
    assert signed_in.json()["status"] == UserStatus.suspended


@pytest.mark.asyncio
async def test_a_suspended_account_stops_appearing_in_pairs(client, db_sessionmaker, reported, fake_storage):
    """The point of the whole exercise. `pairing` filters on active status, so
    this is really a guard against somebody widening that filter later.

    Seen through a bystander, not a reporter: reporting blocks by default, so
    a reporter cannot see the subject either way and the test would pass
    without suspension doing anything at all.
    """
    from backend.pairing import _eligible_candidates

    subject_id = _uid(reported["subject"])
    bystander = await onboard(
        client, "nobody", visible_as=["woman"], interested_in=["man"], store=fake_storage
    )
    await run_jobs(db_sessionmaker)

    async def visible_to_a_bystander() -> set[str]:
        async with db_sessionmaker() as db:
            viewer = await db.get(User, _uid(bystander))
            candidates = await _eligible_candidates(
                db, viewer=viewer, segment="man", viewer_visible_as=["woman"]
            )
        return {c.user_id for c in candidates}

    # Asserted before as well as after, or the test passes just as happily
    # against a subject who was never eligible for some unrelated reason.
    assert subject_id in await visible_to_a_bystander()
    await _suspend(client, reported)
    assert subject_id not in await visible_to_a_bystander()


@pytest.mark.asyncio
async def test_a_suspension_can_be_lifted(client, db_sessionmaker, reported):
    """A suspension that cannot be undone is one nobody will make on thin
    evidence *or* on good evidence — the cost of being wrong is identical."""
    await _suspend(client, reported)
    subject_id = _uid(reported["subject"])

    back = await client.post(
        f"/api/moderation/subjects/{subject_id}/reinstate", headers=reported["reviewer"]["headers"]
    )
    assert back.status_code == 200

    async with db_sessionmaker() as db:
        assert (await db.get(User, subject_id)).status == UserStatus.active
    assert (await client.get("/api/profile", headers=reported["subject"]["headers"])).status_code == 200


@pytest.mark.asyncio
async def test_a_reviewer_cannot_review_themselves(client, db_sessionmaker, reported):
    reviewer = reported["reviewer"]
    reporter = reported["reporters"][0]
    await client.post(
        "/api/safety/reports",
        headers=reporter["headers"],
        json={"user_id": _uid(reviewer), "reason": "fake"},
    )

    refused = await client.post(
        f"/api/moderation/subjects/{_uid(reviewer)}/decide",
        headers=reviewer["headers"],
        json={"action": "suspend", "note": "no"},
    )
    assert refused.status_code == 400


@pytest.mark.asyncio
async def test_deciding_twice_finds_nothing_left(client, reported):
    subject_id = _uid(reported["subject"])
    payload = {"action": "dismiss", "note": "Nothing in it."}
    first = await client.post(
        f"/api/moderation/subjects/{subject_id}/decide",
        headers=reported["reviewer"]["headers"],
        json=payload,
    )
    assert first.status_code == 200

    again = await client.post(
        f"/api/moderation/subjects/{subject_id}/decide",
        headers=reported["reviewer"]["headers"],
        json=payload,
    )
    assert again.status_code == 400
