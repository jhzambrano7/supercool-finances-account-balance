"""Integration tests for the three account read routes against a real PostgreSQL
(docs/web-ui-plan.md §6.1, §6.1b, §6.2): `GET /accounts/{id}`, `GET /accounts`,
`GET /accounts/{id}/movements`.
"""

from uuid import uuid4

import pytest
from httpx import AsyncClient

from modules.account_balance.adapters.config.seeded_accounts import FUNDING_ACCOUNT_ID

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def _open_user_account(
    client: AsyncClient, *, owner_id: str, purpose: str = "CHECKING"
) -> str:
    response = await client.post(
        "/accounts", json={"owner_id": owner_id, "purpose": purpose, "currency": "USD"}
    )
    assert response.status_code == 201
    account_id: str = response.json()["account_id"]
    return account_id


async def _deposit(client: AsyncClient, *, owner_id: str, account_id: str, amount: int) -> None:
    response = await client.post(
        "/deposits",
        json={"destination_account_id": account_id, "amount": amount, "currency": "USD"},
        headers={"X-Caller-Id": owner_id, "Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 201


# ---------------------------------------------------------------------------
# GET /accounts/{account_id}
# ---------------------------------------------------------------------------


async def test_the_owner_reads_their_own_account(client: AsyncClient) -> None:
    owner_id = str(uuid4())
    account_id = await _open_user_account(client, owner_id=owner_id)
    await _deposit(client, owner_id=owner_id, account_id=account_id, amount=5_000)

    response = await client.get(f"/accounts/{account_id}", headers={"X-Caller-Id": owner_id})

    assert response.status_code == 200
    body = response.json()
    assert body["account_id"] == account_id
    assert body["owner_id"] == owner_id
    assert body["balance"] == 5_000


async def test_a_stranger_is_refused_with_403(client: AsyncClient) -> None:
    owner_id = str(uuid4())
    account_id = await _open_user_account(client, owner_id=owner_id)

    response = await client.get(f"/accounts/{account_id}", headers={"X-Caller-Id": str(uuid4())})

    assert response.status_code == 403


async def test_a_missing_account_is_404(client: AsyncClient) -> None:
    response = await client.get(f"/accounts/{uuid4()}", headers={"X-Caller-Id": str(uuid4())})

    assert response.status_code == 404


async def test_a_missing_caller_header_is_401(client: AsyncClient) -> None:
    response = await client.get(f"/accounts/{uuid4()}")

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET /accounts
# ---------------------------------------------------------------------------


async def test_lists_only_the_caller_s_own_accounts(client: AsyncClient) -> None:
    owner_id = str(uuid4())
    checking = await _open_user_account(client, owner_id=owner_id, purpose="CHECKING")
    savings = await _open_user_account(client, owner_id=owner_id, purpose="SAVINGS")
    await _open_user_account(client, owner_id=str(uuid4()))  # someone else's

    response = await client.get("/accounts", headers={"X-Caller-Id": owner_id})

    assert response.status_code == 200
    account_ids = {item["account_id"] for item in response.json()["items"]}
    assert account_ids == {checking, savings}


async def test_an_owner_with_no_accounts_gets_an_empty_list(client: AsyncClient) -> None:
    response = await client.get("/accounts", headers={"X-Caller-Id": str(uuid4())})

    assert response.status_code == 200
    assert response.json()["items"] == []


async def test_a_missing_caller_header_is_401_for_the_list(client: AsyncClient) -> None:
    response = await client.get("/accounts")

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET /accounts/{account_id}/movements
# ---------------------------------------------------------------------------


async def test_movements_paginate_across_a_page_boundary_newest_first(
    client: AsyncClient,
) -> None:
    owner_id = str(uuid4())
    account_id = await _open_user_account(client, owner_id=owner_id)
    for amount in (1_000, 2_000, 3_000):
        await _deposit(client, owner_id=owner_id, account_id=account_id, amount=amount)

    first_page = await client.get(
        f"/accounts/{account_id}/movements",
        params={"limit": 2},
        headers={"X-Caller-Id": owner_id},
    )
    assert first_page.status_code == 200
    first_body = first_page.json()
    assert len(first_body["items"]) == 2
    assert first_body["next_cursor"] is not None
    # Newest first: the last deposit (3_000) is the first item.
    assert first_body["items"][0]["amount"] == 3_000
    assert first_body["items"][1]["amount"] == 2_000
    for item in first_body["items"]:
        assert item["direction"] == "CREDIT"
        assert item["currency"] == "USD"
        assert item["requested_by"] == owner_id
        # A deposit's counterparty is the platform's FUNDING account -- never the caller's own
        # account id, and never something the caller supplied (there is no SYSTEM account id
        # anywhere in the /deposits request body).
        assert item["counterparty_account_id"] == str(FUNDING_ACCOUNT_ID)

    second_page = await client.get(
        f"/accounts/{account_id}/movements",
        params={"limit": 2, "cursor": first_body["next_cursor"]},
        headers={"X-Caller-Id": owner_id},
    )
    assert second_page.status_code == 200
    second_body = second_page.json()
    assert len(second_body["items"]) == 1
    assert second_body["items"][0]["amount"] == 1_000
    assert second_body["next_cursor"] is None

    first_page_entry_ids = {item["entry_id"] for item in first_body["items"]}
    second_page_entry_ids = {item["entry_id"] for item in second_body["items"]}
    assert first_page_entry_ids.isdisjoint(second_page_entry_ids)


async def test_a_stranger_is_refused_with_403_for_movements(client: AsyncClient) -> None:
    owner_id = str(uuid4())
    account_id = await _open_user_account(client, owner_id=owner_id)

    response = await client.get(
        f"/accounts/{account_id}/movements", headers={"X-Caller-Id": str(uuid4())}
    )

    assert response.status_code == 403


async def test_a_missing_account_is_404_for_movements(client: AsyncClient) -> None:
    response = await client.get(
        f"/accounts/{uuid4()}/movements", headers={"X-Caller-Id": str(uuid4())}
    )

    assert response.status_code == 404


async def test_a_missing_caller_header_is_401_for_movements(client: AsyncClient) -> None:
    response = await client.get(f"/accounts/{uuid4()}/movements")

    assert response.status_code == 401


async def test_a_malformed_cursor_is_422(client: AsyncClient) -> None:
    owner_id = str(uuid4())
    account_id = await _open_user_account(client, owner_id=owner_id)

    response = await client.get(
        f"/accounts/{account_id}/movements",
        params={"cursor": "not-a-real-cursor"},
        headers={"X-Caller-Id": owner_id},
    )

    assert response.status_code == 422
