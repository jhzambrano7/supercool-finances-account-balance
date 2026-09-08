"""Integration test for `POST /accounts/{account_id}/close` against a real PostgreSQL
(docs/prd.md §7.2)."""

from uuid import uuid4

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def _open_user_account(client: AsyncClient, *, owner_id: str) -> str:
    response = await client.post(
        "/accounts", json={"owner_id": owner_id, "purpose": "CHECKING", "currency": "USD"}
    )
    assert response.status_code == 201
    account_id: str = response.json()["account_id"]
    return account_id


async def test_the_owner_closes_their_own_zero_balance_account(client: AsyncClient) -> None:
    owner_id = str(uuid4())
    account_id = await _open_user_account(client, owner_id=owner_id)

    response = await client.post(f"/accounts/{account_id}/close", headers={"X-Caller-Id": owner_id})

    assert response.status_code == 200
    body = response.json()
    assert body["account_id"] == account_id
    assert body["status"] == "CLOSED"

    # A closed account can still be read (docs/prd.md §7.2: "history does not disappear").
    read_back = await client.get(f"/accounts/{account_id}", headers={"X-Caller-Id": owner_id})
    assert read_back.status_code == 200
    assert read_back.json()["status"] == "CLOSED"


async def test_a_stranger_is_refused_with_403(client: AsyncClient) -> None:
    owner_id = str(uuid4())
    account_id = await _open_user_account(client, owner_id=owner_id)

    response = await client.post(
        f"/accounts/{account_id}/close", headers={"X-Caller-Id": str(uuid4())}
    )

    assert response.status_code == 403


async def test_a_missing_account_is_reported_as_not_found(client: AsyncClient) -> None:
    response = await client.post(
        f"/accounts/{uuid4()}/close", headers={"X-Caller-Id": str(uuid4())}
    )

    assert response.status_code == 404


async def test_a_non_zero_balance_is_refused_with_422(client: AsyncClient) -> None:
    owner_id = str(uuid4())
    account_id = await _open_user_account(client, owner_id=owner_id)
    deposit_response = await client.post(
        "/deposits",
        json={"destination_account_id": account_id, "amount": 500, "currency": "USD"},
        headers={"X-Caller-Id": owner_id, "Idempotency-Key": str(uuid4())},
    )
    assert deposit_response.status_code == 201

    response = await client.post(f"/accounts/{account_id}/close", headers={"X-Caller-Id": owner_id})

    assert response.status_code == 422

    # Emptying the account first is a transfer like any other (docs/prd.md §7.2), and then it
    # closes.
    withdraw_response = await client.post(
        "/withdrawals",
        json={"source_account_id": account_id, "amount": 500, "currency": "USD"},
        headers={"X-Caller-Id": owner_id, "Idempotency-Key": str(uuid4())},
    )
    assert withdraw_response.status_code == 201
    close_response = await client.post(
        f"/accounts/{account_id}/close", headers={"X-Caller-Id": owner_id}
    )
    assert close_response.status_code == 200


async def test_an_already_closed_account_is_refused_with_422(client: AsyncClient) -> None:
    owner_id = str(uuid4())
    account_id = await _open_user_account(client, owner_id=owner_id)
    first = await client.post(f"/accounts/{account_id}/close", headers={"X-Caller-Id": owner_id})
    assert first.status_code == 200

    second = await client.post(f"/accounts/{account_id}/close", headers={"X-Caller-Id": owner_id})

    assert second.status_code == 422
