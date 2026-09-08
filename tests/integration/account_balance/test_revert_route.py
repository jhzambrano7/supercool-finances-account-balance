"""Integration tests for `POST /transfers/{transfer_id}/reversals` against a
real PostgreSQL.

Per openspec/specs/revert/spec.md's Testing Strategy: the full route,
including the PRD §7.3 worked example (Ana/Bruno) and the double-reversal
conflict -- the parts that do not need two concurrent connections (that is
`test_concurrent_reversal_locking.py`).
"""

import logging
from collections.abc import Callable
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from modules.account_balance.adapters.config.admin_principal import ADMIN_PRINCIPAL_ID
from modules.account_balance.adapters.config.seeded_accounts import (
    FUNDING_ACCOUNT_ID,
    SETTLEMENT_ACCOUNT_ID,
)
from modules.account_balance.adapters.outbound.repositories.sql.sql_account_repository import (
    SqlAccountRepository,
)
from modules.account_balance.application.gateways.models.find_accounts_criteria import (
    FindAccountByAccountId,
)
from modules.account_balance.domain.account import UserAccount
from modules.account_balance.domain.identifiers import AccountId

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

_logger = logging.getLogger(__name__)
_ADMIN_ID = str(ADMIN_PRINCIPAL_ID)


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
    repository = SqlAccountRepository(_logger, session_factory)
    account = await repository.get(criteria=FindAccountByAccountId(AccountId(UUID(account_id))))
    assert isinstance(account, UserAccount)
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

    response = await client.post(
        f"/transfers/{transfer_id}/reversals",
        headers=_headers(caller_id=_ADMIN_ID, idempotency_key=str(uuid4())),
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
        headers=_headers(caller_id=_ADMIN_ID, idempotency_key=str(uuid4())),
    )

    assert response.status_code == 201
    assert await _balance_of(session_factory, account_id=bruno) == -80
    assert await _balance_of(session_factory, account_id=ana) == 100


async def test_a_reversal_by_the_admin_does_not_require_owning_either_leg(
    client: AsyncClient,
) -> None:
    """R1: the admin principal has no ownership relationship to either account and can still

    reverse the transfer -- there is no ownership check at all, only the authorization check.
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

    response = await client.post(
        f"/transfers/{transfer_id}/reversals",
        headers=_headers(caller_id=_ADMIN_ID, idempotency_key=str(uuid4())),
    )

    assert response.status_code == 201


async def test_a_non_admin_caller_is_rejected(client: AsyncClient) -> None:
    """R1: a caller who is not the platform's one authorized admin principal is rejected,

    even one who would otherwise be a legitimate customer of the platform.
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

    assert response.status_code == 403


async def test_reversing_a_nonexistent_transfer_is_not_found(client: AsyncClient) -> None:
    response = await client.post(
        f"/transfers/{uuid4()}/reversals",
        headers=_headers(caller_id=_ADMIN_ID, idempotency_key=str(uuid4())),
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
        headers=_headers(caller_id=_ADMIN_ID, idempotency_key=str(uuid4())),
    )
    assert first.status_code == 201

    second = await client.post(
        f"/transfers/{transfer_id}/reversals",
        headers=_headers(caller_id=_ADMIN_ID, idempotency_key=str(uuid4())),
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
        headers=_headers(caller_id=_ADMIN_ID, idempotency_key=str(uuid4())),
    )
    assert first_reversal.status_code == 201
    reversal_id = first_reversal.json()["transfer_id"]

    second_reversal = await client.post(
        f"/transfers/{reversal_id}/reversals",
        headers=_headers(caller_id=_ADMIN_ID, idempotency_key=str(uuid4())),
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
    operator_id = _ADMIN_ID
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


async def test_a_blank_idempotency_key_is_unprocessable_not_a_server_error(
    client: AsyncClient,
) -> None:
    """Same bug class `transfer`'s own route found and fixed twice
    (`5635aec`, `9b0427d`): InvalidIdempotencyKeyError is a DomainError
    reachable straight from this endpoint's own input and must not fall
    through to the generic 500 default. The mapping was already correct
    when this test was added (found missing only as test *coverage* by an
    independent review, not as a live bug) -- this closes that gap.
    """
    owner_id = str(uuid4())
    account_id = await _open_user_account(client, owner_id=owner_id)
    transfer_response = await client.post(
        "/transfers",
        json={
            "source_account_id": str(FUNDING_ACCOUNT_ID),
            "destination_account_id": account_id,
            "amount": 500,
            "currency": "USD",
        },
        headers=_headers(caller_id=owner_id, idempotency_key=str(uuid4())),
    )
    transfer_id = transfer_response.json()["transfer_id"]

    response = await client.post(
        f"/transfers/{transfer_id}/reversals",
        headers=_headers(caller_id=str(uuid4()), idempotency_key="   "),
    )

    assert response.status_code == 422
