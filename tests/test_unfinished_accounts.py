"""An account that has not finished joining cannot take part yet.

Onboarding is where somebody gives consent, adds a photo that is screened, and
writes the profile other people will read. Status stays `onboarding` until
`submit` accepts all of it. The pair view, the inbox and messaging were guarded
only against *suspended* accounts, so an account that answered the first
question — who it wants to see — and then simply stopped could still be served
pairs, unlock people, and write to them: a profile-less, photo-less, unscreened
account reaching real members, and appearing on their Keep choosing you as a
blank card.
"""

from __future__ import annotations

import pytest

from tests.conftest import onboard, register_and_verify, run_jobs


async def _half_joined(client):
    """Registered, and told the app who it wants to see — and nothing else."""
    account = await register_and_verify(client, "halfway")
    patched = await client.patch(
        "/api/profile",
        headers=account["headers"],
        json={"visible_as": ["man"], "interested_in": ["woman"]},
    )
    assert patched.status_code == 200, patched.text
    return account


@pytest.mark.asyncio
async def test_an_unfinished_account_is_not_served_pairs(client, db_sessionmaker, fake_storage):
    for name in ("one", "two"):
        await onboard(client, name, visible_as=["woman"], interested_in=["man"], store=fake_storage)
    await run_jobs(db_sessionmaker)
    halfway = await _half_joined(client)

    response = await client.get("/api/pairs/next", headers=halfway["headers"])

    assert response.status_code == 403, response.text


@pytest.mark.asyncio
async def test_a_finished_account_in_the_same_pool_is_served_pairs(client, db_sessionmaker, fake_storage):
    """The control: the same pool, one account that did finish."""
    for name in ("one", "two"):
        await onboard(client, name, visible_as=["woman"], interested_in=["man"], store=fake_storage)
    viewer = await onboard(client, "done", visible_as=["man"], interested_in=["woman"], store=fake_storage)
    await run_jobs(db_sessionmaker)

    response = await client.get("/api/pairs/next", headers=viewer["headers"])

    assert response.status_code == 200
    assert response.json()["pair"] is not None


@pytest.mark.asyncio
async def test_an_unfinished_account_cannot_reach_the_inbox(client):
    halfway = await _half_joined(client)
    for path in ("/api/connections", "/api/connections/counts"):
        response = await client.get(path, headers=halfway["headers"])
        assert response.status_code == 403, f"{path}: {response.text}"


@pytest.mark.asyncio
async def test_an_unfinished_account_can_still_finish_joining(client, fake_storage):
    """The guard must not lock somebody out of the very steps that lift it."""
    halfway = await _half_joined(client)
    for method, path in (("GET", "/api/profile"), ("GET", "/api/profile/options"), ("GET", "/api/media")):
        response = await client.request(method, path, headers=halfway["headers"])
        assert response.status_code == 200, f"{method} {path}: {response.text}"
    consent = await client.post("/api/account/consent", headers=halfway["headers"])
    assert consent.status_code == 201, consent.text
