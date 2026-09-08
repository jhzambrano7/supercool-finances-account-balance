"""Integration test for T6's deterministic locking, against a real PostgreSQL.

Per openspec/specs/transfer/spec.md's Testing Strategy and its own note: "Not
independently testable at the unit level without a real database transaction
-- covered by integration tests asserting lock behavior via two concurrent
connections." This is that test -- the one claim in the spec that cannot be
demonstrated any other way.
"""

import asyncio
from uuid import uuid4

import pytest
from httpx import AsyncClient, Response

from modules.account_balance.adapters.config.seeded_accounts import (
    FUNDING_ACCOUNT_ID,
    SETTLEMENT_ACCOUNT_ID,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def test_two_concurrent_debits_of_the_same_account_serialize_instead_of_corrupting_balance(
    client: AsyncClient,
) -> None:
    """GIVEN a USER account with balance 100, two concurrent transfers each debiting 60 from it,
    WHEN both are posted concurrently, THEN one succeeds and the other is rejected by I2
    (`InsufficientFundsError`, 422) rather than both succeeding and driving the balance negative."""
    owner_id = str(uuid4())
    open_response = await client.post(
        "/accounts", json={"owner_id": owner_id, "purpose": "CHECKING", "currency": "USD"}
    )
    account_id = open_response.json()["account_id"]
    await client.post(
        "/transfers",
        json={
            "source_account_id": str(FUNDING_ACCOUNT_ID),
            "destination_account_id": account_id,
            "amount": 100,
            "currency": "USD",
        },
        headers={"X-Caller-Id": owner_id, "Idempotency-Key": str(uuid4())},
    )

    async def _withdraw(idempotency_key: str) -> Response:
        return await client.post(
            "/transfers",
            json={
                "source_account_id": account_id,
                "destination_account_id": str(SETTLEMENT_ACCOUNT_ID),
                "amount": 60,
                "currency": "USD",
            },
            headers={"X-Caller-Id": owner_id, "Idempotency-Key": idempotency_key},
        )

    first, second = await asyncio.gather(_withdraw(str(uuid4())), _withdraw(str(uuid4())))

    statuses = sorted([first.status_code, second.status_code])
    assert statuses == [201, 422]

    balance_response = await client.post(
        "/transfers",
        json={
            "source_account_id": account_id,
            "destination_account_id": str(SETTLEMENT_ACCOUNT_ID),
            "amount": 1,
            "currency": "USD",
        },
        headers={"X-Caller-Id": owner_id, "Idempotency-Key": str(uuid4())},
    )
    # 100 - 60 (the winner) - 1 (this probe) = 39, never negative -- proves
    # the loser's debit was never applied, not merely that it returned 422.
    assert balance_response.status_code == 201


async def test_a_losing_concurrent_request_replays_the_winner_against_real_postgres(
    client: AsyncClient,
) -> None:
    """T5's race, against real concurrent connections, not a fake: the
    account is funded with *exactly* enough for one withdrawal. Found by an
    independent review that the pre-fix use case raised
    `InsufficientFundsError` for the loser here instead of replaying the
    winner -- the loser's `get_for_update` would observe the winner's
    already-applied debit and fail the domain check before ever reaching the
    idempotency insert its own recovery path depended on. Both requests share
    one `Idempotency-Key`, so the correct outcome is `201, 201` with the same
    `transfer_id`, not `201, 422`.
    """
    owner_id = str(uuid4())
    open_response = await client.post(
        "/accounts", json={"owner_id": owner_id, "purpose": "CHECKING", "currency": "USD"}
    )
    account_id = open_response.json()["account_id"]
    await client.post(
        "/transfers",
        json={
            "source_account_id": str(FUNDING_ACCOUNT_ID),
            "destination_account_id": account_id,
            "amount": 500,
            "currency": "USD",
        },
        headers={"X-Caller-Id": owner_id, "Idempotency-Key": str(uuid4())},
    )
    shared_key = str(uuid4())

    async def _withdraw() -> Response:
        return await client.post(
            "/transfers",
            json={
                "source_account_id": account_id,
                "destination_account_id": str(SETTLEMENT_ACCOUNT_ID),
                "amount": 500,
                "currency": "USD",
            },
            headers={"X-Caller-Id": owner_id, "Idempotency-Key": shared_key},
        )

    first, second = await asyncio.gather(_withdraw(), _withdraw())

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["transfer_id"] == second.json()["transfer_id"]
