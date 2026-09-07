"""Integration test for T6's deterministic locking, against a real PostgreSQL.

Per openspec/specs/transfer/spec.md's Testing Strategy and its own note: "Not
independently testable at the unit level without a real database transaction
-- covered by integration tests asserting lock behavior via two concurrent
connections." This is that test -- the one claim in the spec that cannot be
demonstrated any other way.
"""

import asyncio
from collections.abc import AsyncIterator, Iterator
from uuid import uuid4

import pytest
from dependency_injector import providers
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from modules.account_balance.adapters.config.seeded_accounts import (
    FUNDING_ACCOUNT_ID,
    SETTLEMENT_ACCOUNT_ID,
)
from modules.shared.adapters.config.settings import Settings
from modules.shared.adapters.inbound.api.app import create_app

pytestmark = pytest.mark.integration


@pytest.fixture
def app(postgres_url: str) -> Iterator[FastAPI]:
    application = create_app()
    container = application.container  # type: ignore[attr-defined]
    container.settings.override(providers.Object(Settings(database_url=postgres_url)))
    yield application
    container.settings.reset_override()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_two_concurrent_debits_of_the_same_account_serialize_instead_of_corrupting_balance(
    client: AsyncClient,
) -> None:
    """GIVEN a USER account with balance 100, two concurrent transfers each

    debiting 60 from it, WHEN both are posted concurrently, THEN one
    succeeds and the other is rejected by I2 (`InsufficientFundsError`,
    422) rather than both succeeding and driving the balance negative.
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
