"""Integration tests for `POST /deposits` and `POST /withdrawals` against a real PostgreSQL.

Per openspec/specs/transfer/spec.md's "The design, settled": unlike `POST /transfers`
(`test_transfer_route.py`), these endpoints never take a platform account id from the caller --
they resolve the seeded `FUNDING`/`SETTLEMENT` account for the requested currency themselves.
"""

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


def _headers(*, caller_id: str, idempotency_key: str) -> dict[str, str]:
    return {"X-Caller-Id": caller_id, "Idempotency-Key": idempotency_key}


async def test_a_deposit_credits_the_destination_without_the_caller_naming_a_system_account(
    client: AsyncClient,
) -> None:
    owner_id = str(uuid4())
    account_id = await _open_user_account(client, owner_id=owner_id)

    response = await client.post(
        "/deposits",
        json={"destination_account_id": account_id, "amount": 5_000, "currency": "USD"},
        headers=_headers(caller_id=owner_id, idempotency_key=str(uuid4())),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["destination_account_id"] == account_id
    assert body["amount"] == 5_000
    assert len(body["entries"]) == 2


async def test_a_withdrawal_debits_the_source_without_the_caller_naming_a_system_account(
    client: AsyncClient,
) -> None:
    owner_id = str(uuid4())
    account_id = await _open_user_account(client, owner_id=owner_id)
    await client.post(
        "/deposits",
        json={"destination_account_id": account_id, "amount": 10_000, "currency": "USD"},
        headers=_headers(caller_id=owner_id, idempotency_key=str(uuid4())),
    )

    response = await client.post(
        "/withdrawals",
        json={"source_account_id": account_id, "amount": 2_500, "currency": "USD"},
        headers=_headers(caller_id=owner_id, idempotency_key=str(uuid4())),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["source_account_id"] == account_id
    assert body["amount"] == 2_500


async def test_a_deposit_in_a_currency_with_no_seeded_funding_account_is_unavailable(
    client: AsyncClient,
) -> None:
    """`EUR` passes `Currency`'s ISO-4217-shape check but has no seeded `FUNDING` account -- a
    platform-configuration gap, not a client mistake (CurrencyNotOperationalError -> 503)."""
    response = await client.post(
        "/deposits",
        json={"destination_account_id": str(uuid4()), "amount": 100, "currency": "EUR"},
        headers=_headers(caller_id=str(uuid4()), idempotency_key=str(uuid4())),
    )

    assert response.status_code == 503


async def test_a_withdrawal_in_a_currency_with_no_seeded_settlement_account_is_unavailable(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/withdrawals",
        json={"source_account_id": str(uuid4()), "amount": 100, "currency": "EUR"},
        headers=_headers(caller_id=str(uuid4()), idempotency_key=str(uuid4())),
    )

    assert response.status_code == 503


async def test_a_withdrawal_the_caller_does_not_own_is_forbidden(client: AsyncClient) -> None:
    owner_id = str(uuid4())
    stranger = str(uuid4())
    account_id = await _open_user_account(client, owner_id=owner_id)
    await client.post(
        "/deposits",
        json={"destination_account_id": account_id, "amount": 10_000, "currency": "USD"},
        headers=_headers(caller_id=owner_id, idempotency_key=str(uuid4())),
    )

    response = await client.post(
        "/withdrawals",
        json={"source_account_id": account_id, "amount": 1_000, "currency": "USD"},
        headers=_headers(caller_id=stranger, idempotency_key=str(uuid4())),
    )

    assert response.status_code == 403


async def test_a_retried_deposit_with_the_same_body_replays_the_original(
    client: AsyncClient,
) -> None:
    owner_id = str(uuid4())
    account_id = await _open_user_account(client, owner_id=owner_id)
    key = str(uuid4())
    payload = {"destination_account_id": account_id, "amount": 1_500, "currency": "USD"}

    first = await client.post(
        "/deposits", json=payload, headers=_headers(caller_id=owner_id, idempotency_key=key)
    )
    second = await client.post(
        "/deposits", json=payload, headers=_headers(caller_id=owner_id, idempotency_key=key)
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["transfer_id"] == first.json()["transfer_id"]


async def test_a_withdrawal_overdrawing_the_account_is_unprocessable(client: AsyncClient) -> None:
    owner_id = str(uuid4())
    account_id = await _open_user_account(client, owner_id=owner_id)

    response = await client.post(
        "/withdrawals",
        json={"source_account_id": account_id, "amount": 100, "currency": "USD"},
        headers=_headers(caller_id=owner_id, idempotency_key=str(uuid4())),
    )

    assert response.status_code == 422


async def test_a_missing_caller_header_is_rejected_before_any_account_is_loaded(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/deposits",
        json={"destination_account_id": str(uuid4()), "amount": 100, "currency": "USD"},
        headers={"Idempotency-Key": str(uuid4())},
    )

    assert response.status_code == 401
