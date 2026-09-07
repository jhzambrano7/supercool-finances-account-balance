"""Integration tests for `POST /transfers` against a real PostgreSQL.

Per openspec/specs/transfer/spec.md's Testing Strategy: the full route for
each of the four movement shapes (T1's table), plus idempotent replay/
conflict (T3, T4) and the missing-header rejection (T2) -- the parts that do
not need two concurrent connections (that is `test_concurrent_transfer_locking.py`).
"""

from collections.abc import AsyncIterator, Iterator
from uuid import uuid4

import pytest
from dependency_injector import providers
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

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


async def _open_user_account(client: AsyncClient, *, owner_id: str) -> str:
    response = await client.post(
        "/accounts", json={"owner_id": owner_id, "purpose": "CHECKING", "currency": "USD"}
    )
    assert response.status_code == 201
    account_id: str = response.json()["account_id"]
    return account_id


def _headers(*, caller_id: str, idempotency_key: str) -> dict[str, str]:
    return {"X-Caller-Id": caller_id, "Idempotency-Key": idempotency_key}


async def test_deposit_is_authorized_by_destination(client: AsyncClient) -> None:
    owner_id = str(uuid4())
    account_id = await _open_user_account(client, owner_id=owner_id)

    response = await client.post(
        "/transfers",
        json={
            "source_account_id": str(FUNDING_ACCOUNT_ID),
            "destination_account_id": account_id,
            "amount": 5_000,
            "currency": "USD",
        },
        headers=_headers(caller_id=owner_id, idempotency_key=str(uuid4())),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["source_account_id"] == str(FUNDING_ACCOUNT_ID)
    assert body["destination_account_id"] == account_id
    assert body["amount"] == 5_000
    assert len(body["entries"]) == 2


async def test_withdraw_is_authorized_by_source(client: AsyncClient) -> None:
    owner_id = str(uuid4())
    account_id = await _open_user_account(client, owner_id=owner_id)
    # Fund the account first via a deposit -- I2 forbids a USER account from
    # going negative, so the withdrawal needs something to draw down.
    await client.post(
        "/transfers",
        json={
            "source_account_id": str(FUNDING_ACCOUNT_ID),
            "destination_account_id": account_id,
            "amount": 10_000,
            "currency": "USD",
        },
        headers=_headers(caller_id=owner_id, idempotency_key=str(uuid4())),
    )

    response = await client.post(
        "/transfers",
        json={
            "source_account_id": account_id,
            "destination_account_id": str(SETTLEMENT_ACCOUNT_ID),
            "amount": 2_500,
            "currency": "USD",
        },
        headers=_headers(caller_id=owner_id, idempotency_key=str(uuid4())),
    )

    assert response.status_code == 201
    assert response.json()["source_account_id"] == account_id


async def test_customer_to_customer_transfer_is_authorized_by_source_only(
    client: AsyncClient,
) -> None:
    owner_a = str(uuid4())
    owner_b = str(uuid4())
    account_a = await _open_user_account(client, owner_id=owner_a)
    account_b = await _open_user_account(client, owner_id=owner_b)
    await client.post(
        "/transfers",
        json={
            "source_account_id": str(FUNDING_ACCOUNT_ID),
            "destination_account_id": account_a,
            "amount": 10_000,
            "currency": "USD",
        },
        headers=_headers(caller_id=owner_a, idempotency_key=str(uuid4())),
    )

    response = await client.post(
        "/transfers",
        json={
            "source_account_id": account_a,
            "destination_account_id": account_b,
            "amount": 1_000,
            "currency": "USD",
        },
        headers=_headers(caller_id=owner_a, idempotency_key=str(uuid4())),
    )

    assert response.status_code == 201


async def test_a_transfer_debiting_an_account_the_caller_does_not_own_is_rejected(
    client: AsyncClient,
) -> None:
    owner_a = str(uuid4())
    stranger = str(uuid4())
    account_a = await _open_user_account(client, owner_id=owner_a)
    account_b = await _open_user_account(client, owner_id=str(uuid4()))
    await client.post(
        "/transfers",
        json={
            "source_account_id": str(FUNDING_ACCOUNT_ID),
            "destination_account_id": account_a,
            "amount": 10_000,
            "currency": "USD",
        },
        headers=_headers(caller_id=owner_a, idempotency_key=str(uuid4())),
    )

    response = await client.post(
        "/transfers",
        json={
            "source_account_id": account_a,
            "destination_account_id": account_b,
            "amount": 1_000,
            "currency": "USD",
        },
        headers=_headers(caller_id=stranger, idempotency_key=str(uuid4())),
    )

    assert response.status_code == 403


async def test_a_retried_transfer_with_the_same_body_replays_the_original(
    client: AsyncClient,
) -> None:
    owner_id = str(uuid4())
    account_id = await _open_user_account(client, owner_id=owner_id)
    key = str(uuid4())
    payload = {
        "source_account_id": str(FUNDING_ACCOUNT_ID),
        "destination_account_id": account_id,
        "amount": 1_500,
        "currency": "USD",
    }

    first = await client.post(
        "/transfers", json=payload, headers=_headers(caller_id=owner_id, idempotency_key=key)
    )
    second = await client.post(
        "/transfers", json=payload, headers=_headers(caller_id=owner_id, idempotency_key=key)
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["transfer_id"] == first.json()["transfer_id"]


async def test_the_same_key_with_a_different_body_is_a_conflict(client: AsyncClient) -> None:
    owner_id = str(uuid4())
    account_id = await _open_user_account(client, owner_id=owner_id)
    key = str(uuid4())

    first = await client.post(
        "/transfers",
        json={
            "source_account_id": str(FUNDING_ACCOUNT_ID),
            "destination_account_id": account_id,
            "amount": 1_500,
            "currency": "USD",
        },
        headers=_headers(caller_id=owner_id, idempotency_key=key),
    )
    second = await client.post(
        "/transfers",
        json={
            "source_account_id": str(FUNDING_ACCOUNT_ID),
            "destination_account_id": account_id,
            "amount": 9_999,
            "currency": "USD",
        },
        headers=_headers(caller_id=owner_id, idempotency_key=key),
    )

    assert first.status_code == 201
    assert second.status_code == 409


async def test_a_missing_caller_header_is_rejected_before_any_account_is_loaded(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/transfers",
        json={
            "source_account_id": str(FUNDING_ACCOUNT_ID),
            "destination_account_id": str(uuid4()),
            "amount": 100,
            "currency": "USD",
        },
        headers={"Idempotency-Key": str(uuid4())},
    )

    assert response.status_code == 401


async def test_a_malformed_caller_header_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/transfers",
        json={
            "source_account_id": str(FUNDING_ACCOUNT_ID),
            "destination_account_id": str(uuid4()),
            "amount": 100,
            "currency": "USD",
        },
        headers={"X-Caller-Id": "not-a-uuid", "Idempotency-Key": str(uuid4())},
    )

    assert response.status_code == 401


async def test_insufficient_funds_is_unprocessable(client: AsyncClient) -> None:
    owner_id = str(uuid4())
    account_id = await _open_user_account(client, owner_id=owner_id)

    response = await client.post(
        "/transfers",
        json={
            "source_account_id": account_id,
            "destination_account_id": str(SETTLEMENT_ACCOUNT_ID),
            "amount": 100,
            "currency": "USD",
        },
        headers=_headers(caller_id=owner_id, idempotency_key=str(uuid4())),
    )

    assert response.status_code == 422


async def test_a_non_positive_amount_is_unprocessable_not_a_server_error(
    client: AsyncClient,
) -> None:
    owner_id = str(uuid4())
    account_id = await _open_user_account(client, owner_id=owner_id)

    response = await client.post(
        "/transfers",
        json={
            "source_account_id": str(FUNDING_ACCOUNT_ID),
            "destination_account_id": account_id,
            "amount": 0,
            "currency": "USD",
        },
        headers=_headers(caller_id=owner_id, idempotency_key=str(uuid4())),
    )

    assert response.status_code == 422


async def test_a_blank_idempotency_key_is_unprocessable_not_a_server_error(
    client: AsyncClient,
) -> None:
    owner_id = str(uuid4())
    account_id = await _open_user_account(client, owner_id=owner_id)

    response = await client.post(
        "/transfers",
        json={
            "source_account_id": str(FUNDING_ACCOUNT_ID),
            "destination_account_id": account_id,
            "amount": 100,
            "currency": "USD",
        },
        headers=_headers(caller_id=owner_id, idempotency_key="   "),
    )

    assert response.status_code == 422


async def test_a_lowercase_currency_code_is_unprocessable_not_a_server_error(
    client: AsyncClient,
) -> None:
    """`ZZZ` (three uppercase letters) passes `Currency`'s ISO-4217-*shape*
    check even though it names no real currency -- that is a pre-existing,
    deliberate simplification (`Currency` validates format, not a whitelist)
    and not this test's concern. `usd` (lowercase) is what actually fails the
    regex and is the only way to reach `InvalidCurrencyError` through this
    endpoint, since the request schema already pins the field to 3 characters.
    """
    owner_id = str(uuid4())
    account_id = await _open_user_account(client, owner_id=owner_id)

    response = await client.post(
        "/transfers",
        json={
            "source_account_id": str(FUNDING_ACCOUNT_ID),
            "destination_account_id": account_id,
            "amount": 100,
            "currency": "usd",
        },
        headers=_headers(caller_id=owner_id, idempotency_key=str(uuid4())),
    )

    assert response.status_code == 422
