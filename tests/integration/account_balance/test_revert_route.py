"""Integration tests for `POST /transfers/{transfer_id}/reversals` against a
real PostgreSQL.

Per openspec/specs/revert/spec.md's Testing Strategy: the full route,
including the PRD §7.3 worked example (Ana/Bruno) and the double-reversal
conflict -- the parts that do not need two concurrent connections (that is
`test_concurrent_reversal_locking.py`).
"""

from collections.abc import AsyncIterator, Callable, Iterator
from uuid import UUID, uuid4

import pytest
from dependency_injector import providers
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from modules.account_balance.adapters.config.seeded_accounts import (
    FUNDING_ACCOUNT_ID,
    SETTLEMENT_ACCOUNT_ID,
)
from modules.account_balance.adapters.outbound.repositories.sql.account_repository import (
    SqlAccountRepository,
)
from modules.account_balance.domain.identifiers import AccountId
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


async def _deposit(client: AsyncClient, *, account_id: str, amount: int, owner_id: str) -> None:
    response = await client.post(
        "/transfers",
        json={
            "source_account_id": str(FUNDING_ACCOUNT_ID),
            "destination_account_id": account_id,
            "amount": amount,
            "currency": "USD",
        },
        headers=_headers(caller_id=owner_id, idempotency_key=str(uuid4())),
    )
    assert response.status_code == 201


async def _balance_of(session_factory: Callable[[], AsyncSession], *, account_id: str) -> int:
    repository = SqlAccountRepository(session_factory)
    account = await repository.get(AccountId(UUID(account_id)))
    assert account is not None
    return account.balance.amount


async def test_a_completed_transfer_is_reversed(
    client: AsyncClient, session_factory: Callable[[], AsyncSession]
) -> None:
    owner_a = str(uuid4())
    owner_b = str(uuid4())
    account_a = await _open_user_account(client, owner_id=owner_a)
    account_b = await _open_user_account(client, owner_id=owner_b)
    await _deposit(client, account_id=account_a, amount=10_000, owner_id=owner_a)
    transfer_response = await client.post(
        "/transfers",
        json={
            "source_account_id": account_a,
            "destination_account_id": account_b,
            "amount": 100,
            "currency": "USD",
        },
        headers=_headers(caller_id=owner_a, idempotency_key=str(uuid4())),
    )
    transfer_id = transfer_response.json()["transfer_id"]
    operator_id = str(uuid4())

    response = await client.post(
        f"/transfers/{transfer_id}/reversals",
        headers=_headers(caller_id=operator_id, idempotency_key=str(uuid4())),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["source_account_id"] == account_b
    assert body["destination_account_id"] == account_a
    assert len(body["entries"]) == 2
    assert await _balance_of(session_factory, account_id=account_a) == 10_000
    assert await _balance_of(session_factory, account_id=account_b) == 0


async def test_prd_7_3_worked_example_recipient_goes_negative(
    client: AsyncClient, session_factory: Callable[[], AsyncSession]
) -> None:
    """Ana sends 100 to Bruno, Bruno spends 80 elsewhere, an operator

    reverses: the reversal posts in full, Bruno's balance becomes -80, Ana
    is made whole (PRD §7.3's own worked example).
    """
    ana_owner = str(uuid4())
    bruno_owner = str(uuid4())
    ana = await _open_user_account(client, owner_id=ana_owner)
    bruno = await _open_user_account(client, owner_id=bruno_owner)
    await _deposit(client, account_id=ana, amount=100, owner_id=ana_owner)
    transfer_response = await client.post(
        "/transfers",
        json={
            "source_account_id": ana,
            "destination_account_id": bruno,
            "amount": 100,
            "currency": "USD",
        },
        headers=_headers(caller_id=ana_owner, idempotency_key=str(uuid4())),
    )
    transfer_id = transfer_response.json()["transfer_id"]
    # Bruno spends 80 of the 100 he just received elsewhere.
    withdraw_response = await client.post(
        "/transfers",
        json={
            "source_account_id": bruno,
            "destination_account_id": str(SETTLEMENT_ACCOUNT_ID),
            "amount": 80,
            "currency": "USD",
        },
        headers=_headers(caller_id=bruno_owner, idempotency_key=str(uuid4())),
    )
    assert withdraw_response.status_code == 201

    response = await client.post(
        f"/transfers/{transfer_id}/reversals",
        headers=_headers(caller_id=str(uuid4()), idempotency_key=str(uuid4())),
    )

    assert response.status_code == 201
    assert await _balance_of(session_factory, account_id=bruno) == -80
    assert await _balance_of(session_factory, account_id=ana) == 100


async def test_reversal_does_not_require_the_caller_to_own_either_leg(client: AsyncClient) -> None:
    """R1: an operator with no relationship to either account can still

    reverse the transfer -- there is no ownership check at all.
    """
    owner_a = str(uuid4())
    owner_b = str(uuid4())
    account_a = await _open_user_account(client, owner_id=owner_a)
    account_b = await _open_user_account(client, owner_id=owner_b)
    await _deposit(client, account_id=account_a, amount=1_000, owner_id=owner_a)
    transfer_response = await client.post(
        "/transfers",
        json={
            "source_account_id": account_a,
            "destination_account_id": account_b,
            "amount": 250,
            "currency": "USD",
        },
        headers=_headers(caller_id=owner_a, idempotency_key=str(uuid4())),
    )
    transfer_id = transfer_response.json()["transfer_id"]
    stranger = str(uuid4())

    response = await client.post(
        f"/transfers/{transfer_id}/reversals",
        headers=_headers(caller_id=stranger, idempotency_key=str(uuid4())),
    )

    assert response.status_code == 201


async def test_reversing_a_nonexistent_transfer_is_not_found(client: AsyncClient) -> None:
    response = await client.post(
        f"/transfers/{uuid4()}/reversals",
        headers=_headers(caller_id=str(uuid4()), idempotency_key=str(uuid4())),
    )

    assert response.status_code == 404


async def test_a_second_reversal_of_the_same_transfer_is_a_conflict(client: AsyncClient) -> None:
    owner_a = str(uuid4())
    owner_b = str(uuid4())
    account_a = await _open_user_account(client, owner_id=owner_a)
    account_b = await _open_user_account(client, owner_id=owner_b)
    await _deposit(client, account_id=account_a, amount=1_000, owner_id=owner_a)
    transfer_response = await client.post(
        "/transfers",
        json={
            "source_account_id": account_a,
            "destination_account_id": account_b,
            "amount": 250,
            "currency": "USD",
        },
        headers=_headers(caller_id=owner_a, idempotency_key=str(uuid4())),
    )
    transfer_id = transfer_response.json()["transfer_id"]
    first = await client.post(
        f"/transfers/{transfer_id}/reversals",
        headers=_headers(caller_id=str(uuid4()), idempotency_key=str(uuid4())),
    )
    assert first.status_code == 201

    second = await client.post(
        f"/transfers/{transfer_id}/reversals",
        headers=_headers(caller_id=str(uuid4()), idempotency_key=str(uuid4())),
    )

    assert second.status_code == 409


async def test_reversing_a_reversal_succeeds(client: AsyncClient) -> None:
    owner_a = str(uuid4())
    owner_b = str(uuid4())
    account_a = await _open_user_account(client, owner_id=owner_a)
    account_b = await _open_user_account(client, owner_id=owner_b)
    await _deposit(client, account_id=account_a, amount=1_000, owner_id=owner_a)
    transfer_response = await client.post(
        "/transfers",
        json={
            "source_account_id": account_a,
            "destination_account_id": account_b,
            "amount": 250,
            "currency": "USD",
        },
        headers=_headers(caller_id=owner_a, idempotency_key=str(uuid4())),
    )
    transfer_id = transfer_response.json()["transfer_id"]
    first_reversal = await client.post(
        f"/transfers/{transfer_id}/reversals",
        headers=_headers(caller_id=str(uuid4()), idempotency_key=str(uuid4())),
    )
    assert first_reversal.status_code == 201
    reversal_id = first_reversal.json()["transfer_id"]

    second_reversal = await client.post(
        f"/transfers/{reversal_id}/reversals",
        headers=_headers(caller_id=str(uuid4()), idempotency_key=str(uuid4())),
    )

    assert second_reversal.status_code == 201


async def test_a_retried_reversal_with_the_same_key_replays_the_original(
    client: AsyncClient,
) -> None:
    owner_a = str(uuid4())
    owner_b = str(uuid4())
    account_a = await _open_user_account(client, owner_id=owner_a)
    account_b = await _open_user_account(client, owner_id=owner_b)
    await _deposit(client, account_id=account_a, amount=1_000, owner_id=owner_a)
    transfer_response = await client.post(
        "/transfers",
        json={
            "source_account_id": account_a,
            "destination_account_id": account_b,
            "amount": 250,
            "currency": "USD",
        },
        headers=_headers(caller_id=owner_a, idempotency_key=str(uuid4())),
    )
    transfer_id = transfer_response.json()["transfer_id"]
    operator_id = str(uuid4())
    key = str(uuid4())

    first = await client.post(
        f"/transfers/{transfer_id}/reversals",
        headers=_headers(caller_id=operator_id, idempotency_key=key),
    )
    second = await client.post(
        f"/transfers/{transfer_id}/reversals",
        headers=_headers(caller_id=operator_id, idempotency_key=key),
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["transfer_id"] == first.json()["transfer_id"]


async def test_a_missing_caller_header_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        f"/transfers/{uuid4()}/reversals", headers={"Idempotency-Key": str(uuid4())}
    )

    assert response.status_code == 401
