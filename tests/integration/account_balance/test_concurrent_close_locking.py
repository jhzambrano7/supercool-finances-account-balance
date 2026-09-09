"""Integration test for closing an account under real concurrent load, against a real
PostgreSQL -- the same reasoning `test_concurrent_transfer_locking.py` exists for: `close()`'s
zero-balance check and its write must happen under a lock a concurrent deposit cannot slip
through, and that is not demonstrable at the unit level without a real database transaction.
"""

import asyncio
from uuid import uuid4

import pytest
from httpx import AsyncClient, Response

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def test_a_close_racing_a_deposit_never_leaves_a_closed_account_holding_money(
    client: AsyncClient,
) -> None:
    """GIVEN a zero-balance `USER` account, one request closing it and another depositing into it
    fired concurrently, THEN exactly one succeeds and the account ends up in a consistent state --
    never `CLOSED` with a non-zero balance, and never both `200` and `201`."""
    owner_id = str(uuid4())
    open_response = await client.post(
        "/accounts", json={"owner_id": owner_id, "purpose": "CHECKING", "currency": "USD"}
    )
    assert open_response.status_code == 201, open_response.text
    account_id = open_response.json()["account_id"]

    async def _close() -> Response:
        return await client.post(f"/accounts/{account_id}/close", headers={"X-Caller-Id": owner_id})

    async def _deposit() -> Response:
        return await client.post(
            "/deposits",
            json={"destination_account_id": account_id, "amount": 10, "currency": "USD"},
            headers={"X-Caller-Id": owner_id, "Idempotency-Key": str(uuid4())},
        )

    close_response, deposit_response = await asyncio.gather(_close(), _deposit())

    read_back = await client.get(f"/accounts/{account_id}", headers={"X-Caller-Id": owner_id})
    final = read_back.json()

    if close_response.status_code == 200:
        # The close won the lock first, saw a zero balance, and closed. The deposit then found a
        # CLOSED destination and was refused -- AccountNotOperableError, 422 -- not silently lost.
        assert deposit_response.status_code == 422
        assert final["status"] == "CLOSED"
        assert final["balance"] == 0
    else:
        # The deposit won the lock first and landed. The close then saw a non-zero balance and
        # was refused -- AccountNotEmptyError, 422 -- never closing an account holding money.
        assert close_response.status_code == 422
        assert deposit_response.status_code == 201
        assert final["status"] == "ACTIVE"
        assert final["balance"] == 10
