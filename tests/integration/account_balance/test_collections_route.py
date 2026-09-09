"""Integration tests for `GET /collections` against a real PostgreSQL (PRD §11.3).

The interesting correctness property lives in the SQL window-function query
(`negative_balances.py`), not in any fake -- so the recovered-then-negative-again case (the task's
own worked scenario: negative, cured, negative again must report the *second* episode's start) is
covered here, against the real query, driven only through the public API.

Per-currency grouping of the exposure totals is covered at the unit level instead
(`test_get_collections_report.py::test_exposure_is_grouped_by_currency_not_summed_across_them`):
the migration only seeds a USD `FUNDING`/`SETTLEMENT` pair (T8), so there is no seeded platform
account to drive a real negative MXN balance through the public API here.
"""

import logging
from uuid import uuid4

import pytest
from httpx import AsyncClient

from modules.account_balance.adapters.config.admin_principal import ADMIN_PRINCIPAL_ID
from modules.account_balance.adapters.config.seeded_accounts import (
    FUNDING_ACCOUNT_ID,
    SETTLEMENT_ACCOUNT_ID,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

_logger = logging.getLogger(__name__)
_ADMIN_ID = str(ADMIN_PRINCIPAL_ID)


def _headers(*, caller_id: str, idempotency_key: str | None = None) -> dict[str, str]:
    headers = {"X-Caller-Id": caller_id}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


async def _open_user_account(client: AsyncClient, *, owner_id: str) -> str:
    response = await client.post(
        "/accounts", json={"owner_id": owner_id, "purpose": "CHECKING", "currency": "USD"}
    )
    assert response.status_code == 201
    account_id: str = response.json()["account_id"]
    return account_id


async def _transfer(
    client: AsyncClient,
    *,
    source_account_id: str,
    destination_account_id: str,
    amount: int,
    owner_id: str,
) -> str:
    response = await client.post(
        "/transfers",
        json={
            "source_account_id": source_account_id,
            "destination_account_id": destination_account_id,
            "amount": amount,
            "currency": "USD",
        },
        headers=_headers(caller_id=owner_id, idempotency_key=str(uuid4())),
    )
    assert response.status_code == 201
    transfer_id: str = response.json()["transfer_id"]
    return transfer_id


async def _deposit(client: AsyncClient, *, account_id: str, amount: int, owner_id: str) -> None:
    await _transfer(
        client,
        source_account_id=str(FUNDING_ACCOUNT_ID),
        destination_account_id=account_id,
        amount=amount,
        owner_id=owner_id,
    )


async def _withdraw(client: AsyncClient, *, account_id: str, amount: int, owner_id: str) -> None:
    await _transfer(
        client,
        source_account_id=account_id,
        destination_account_id=str(SETTLEMENT_ACCOUNT_ID),
        amount=amount,
        owner_id=owner_id,
    )


async def _reverse(client: AsyncClient, *, transfer_id: str) -> None:
    response = await client.post(
        f"/transfers/{transfer_id}/reversals",
        headers=_headers(caller_id=_ADMIN_ID, idempotency_key=str(uuid4())),
    )
    assert response.status_code == 201


async def test_the_empty_case_reports_zero(client: AsyncClient) -> None:
    response = await client.get("/collections", headers=_headers(caller_id=_ADMIN_ID))

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 0
    assert body["oldest_negative_since"] is None
    assert body["exposures"] == []
    assert body["accounts"] == []
    assert sum(bucket["count"] for bucket in body["age_buckets"]) == 0


async def test_a_non_admin_caller_is_rejected(client: AsyncClient) -> None:
    response = await client.get("/collections", headers=_headers(caller_id=str(uuid4())))

    assert response.status_code == 403


async def test_a_missing_caller_header_is_rejected(client: AsyncClient) -> None:
    response = await client.get("/collections")

    assert response.status_code == 401


async def test_an_account_at_exactly_zero_is_not_a_collections_case(client: AsyncClient) -> None:
    """A `USER` account whose balance is exactly zero (never negative) must not appear -- this is
    the boundary `balance_amount < 0` in the SQL query exists to get right, not `<= 0`."""
    owner = str(uuid4())
    account = await _open_user_account(client, owner_id=owner)
    await _deposit(client, account_id=account, amount=500, owner_id=owner)
    await _withdraw(client, account_id=account, amount=500, owner_id=owner)

    response = await client.get("/collections", headers=_headers(caller_id=_ADMIN_ID))

    assert response.status_code == 200
    account_ids = {row["account_id"] for row in response.json()["accounts"]}
    assert account not in account_ids


async def test_a_reversal_that_goes_negative_is_a_collections_case(client: AsyncClient) -> None:
    """PRD §7.3's own worked example (Ana sends 100 to Bruno, Bruno spends 80, an operator
    reverses): Bruno's account is the collections case, at exactly -80."""
    ana_owner = str(uuid4())
    bruno_owner = str(uuid4())
    ana = await _open_user_account(client, owner_id=ana_owner)
    bruno = await _open_user_account(client, owner_id=bruno_owner)
    await _deposit(client, account_id=ana, amount=100, owner_id=ana_owner)
    transfer_id = await _transfer(
        client, source_account_id=ana, destination_account_id=bruno, amount=100, owner_id=ana_owner
    )
    await _withdraw(client, account_id=bruno, amount=80, owner_id=bruno_owner)
    await _reverse(client, transfer_id=transfer_id)

    response = await client.get("/collections", headers=_headers(caller_id=_ADMIN_ID))

    assert response.status_code == 200
    body = response.json()
    rows = {row["account_id"]: row for row in body["accounts"]}
    assert bruno in rows
    assert rows[bruno]["balance"] == -80
    assert rows[bruno]["owner_id"] == bruno_owner
    assert ana not in rows
    exposures = {exposure["currency"]: exposure for exposure in body["exposures"]}
    assert exposures["USD"]["total_owed"] >= 80
    assert body["count"] >= 1
    assert body["oldest_negative_since"] is not None


async def test_recovered_then_negative_again_reports_the_second_episode(
    client: AsyncClient,
) -> None:
    """The task's own worked case: an account goes negative, is cured back to `>= 0`, then goes
    negative again -- `negative_since` must be the *second* episode's start, not the first.

    A naive "oldest debit" or "first crossing" answer would report the first reversal's timestamp
    here; the window-function query in `negative_balances.py` must report the second's.
    """
    ana_owner = str(uuid4())
    bruno_owner = str(uuid4())
    ana = await _open_user_account(client, owner_id=ana_owner)
    bruno = await _open_user_account(client, owner_id=bruno_owner)
    await _deposit(client, account_id=ana, amount=1_000, owner_id=ana_owner)

    # Episode 1: Bruno goes negative, then is cured back to exactly zero.
    first_transfer_id = await _transfer(
        client, source_account_id=ana, destination_account_id=bruno, amount=100, owner_id=ana_owner
    )
    await _withdraw(client, account_id=bruno, amount=80, owner_id=bruno_owner)
    await _reverse(client, transfer_id=first_transfer_id)  # Bruno: -80
    await _deposit(client, account_id=bruno, amount=80, owner_id=bruno_owner)  # Bruno: 0, cured

    # Episode 2: Bruno goes negative again, independently.
    second_transfer_id = await _transfer(
        client, source_account_id=ana, destination_account_id=bruno, amount=100, owner_id=ana_owner
    )
    await _withdraw(client, account_id=bruno, amount=80, owner_id=bruno_owner)
    second_reversal_response = await client.post(
        f"/transfers/{second_transfer_id}/reversals",
        headers=_headers(caller_id=_ADMIN_ID, idempotency_key=str(uuid4())),
    )
    assert second_reversal_response.status_code == 201
    second_reversal_occurred_at = second_reversal_response.json()["occurred_at"]

    response = await client.get("/collections", headers=_headers(caller_id=_ADMIN_ID))

    assert response.status_code == 200
    rows = {row["account_id"]: row for row in response.json()["accounts"]}
    assert bruno in rows
    assert rows[bruno]["balance"] == -80
    # The second reversal's own occurred_at is the second episode's start -- not the first
    # reversal's, which would be the wrong (stale) answer.
    assert rows[bruno]["negative_since"] == second_reversal_occurred_at
