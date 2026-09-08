"""Integration test for `POST /accounts` against a real PostgreSQL."""

import asyncio
from uuid import uuid4

import pytest
from httpx import AsyncClient, Response

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def test_first_open_returns_201_created(client: AsyncClient) -> None:
    payload = {"owner_id": str(uuid4()), "purpose": "CHECKING", "currency": "USD"}

    response = await client.post("/accounts", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body["owner_id"] == payload["owner_id"]
    assert body["purpose"] == "CHECKING"
    assert body["currency"] == "USD"
    assert body["balance"] == 0
    assert body["status"] == "ACTIVE"


async def test_retry_returns_200_with_the_same_account(client: AsyncClient) -> None:
    payload = {"owner_id": str(uuid4()), "purpose": "SAVINGS", "currency": "USD"}

    first = await client.post("/accounts", json=payload)
    second = await client.post("/accounts", json=payload)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["account_id"] == first.json()["account_id"]


async def test_a_system_only_purpose_is_rejected(client: AsyncClient) -> None:
    payload = {"owner_id": str(uuid4()), "purpose": "FUNDING", "currency": "USD"}

    response = await client.post("/accounts", json=payload)

    assert response.status_code == 422


async def test_a_genuinely_concurrent_open_conflicts_instead_of_recovering(
    client: AsyncClient,
) -> None:
    """AO4's race, against real concurrent requests: reviewer's own call is
    that the loser is reported as a conflict, not silently recovered into a
    200 -- a client that hits this simply retries and finds the account via
    the natural-key lookup the use case already tries first.
    """
    payload = {"owner_id": str(uuid4()), "purpose": "CHECKING", "currency": "USD"}

    async def _open() -> Response:
        return await client.post("/accounts", json=payload)

    first, second = await asyncio.gather(_open(), _open())

    statuses = sorted([first.status_code, second.status_code])
    assert statuses == [201, 409]
