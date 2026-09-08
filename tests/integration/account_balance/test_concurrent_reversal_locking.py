"""Integration test for R4's partial-unique-index race, against a real
PostgreSQL.

Per openspec/specs/revert/spec.md's Testing Strategy: "two connections, same
original, different keys -- the one claim here that needs a real
transaction, mirroring transfer's own precedent for why that particular test
can't be a unit test." Mirrors
`test_concurrent_transfer_locking.py`'s fixture shape and use of
`asyncio.gather`.
"""

import asyncio
from uuid import uuid4

import pytest
from httpx import AsyncClient, Response

from modules.account_balance.adapters.config.seeded_accounts import FUNDING_ACCOUNT_ID

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


def _headers(*, caller_id: str, idempotency_key: str) -> dict[str, str]:
    return {"X-Caller-Id": caller_id, "Idempotency-Key": idempotency_key}


async def test_two_concurrent_reversal_requests_for_the_same_transfer_never_both_apply(
    client: AsyncClient,
) -> None:
    """GIVEN a posted transfer, WHEN two operators concurrently request a

    reversal of it under *different* idempotency keys, THEN exactly one
    posts and the other is rejected with 409 (`TransferAlreadyReversedError`)
    -- the partial unique index on `transfers.reverses` (R4, design §8),
    not an application-level check-then-act, is what decides the winner.
    """
    owner_id = str(uuid4())
    account_response = await client.post(
        "/accounts", json={"owner_id": owner_id, "purpose": "CHECKING", "currency": "USD"}
    )
    account_id = account_response.json()["account_id"]
    await client.post(
        "/transfers",
        json={
            "source_account_id": str(FUNDING_ACCOUNT_ID),
            "destination_account_id": account_id,
            "amount": 1_000,
            "currency": "USD",
        },
        headers=_headers(caller_id=owner_id, idempotency_key=str(uuid4())),
    )
    transfer_response = await client.post(
        "/transfers",
        json={
            "source_account_id": str(FUNDING_ACCOUNT_ID),
            "destination_account_id": account_id,
            "amount": 250,
            "currency": "USD",
        },
        headers=_headers(caller_id=owner_id, idempotency_key=str(uuid4())),
    )
    transfer_id = transfer_response.json()["transfer_id"]

    async def _reverse(idempotency_key: str) -> Response:
        return await client.post(
            f"/transfers/{transfer_id}/reversals",
            headers=_headers(caller_id=str(uuid4()), idempotency_key=idempotency_key),
        )

    first, second = await asyncio.gather(_reverse(str(uuid4())), _reverse(str(uuid4())))

    statuses = sorted([first.status_code, second.status_code])
    assert statuses == [201, 409]
