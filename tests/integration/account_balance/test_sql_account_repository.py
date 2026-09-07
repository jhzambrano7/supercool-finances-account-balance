"""Integration tests for `SqlAccountRepository` against a real PostgreSQL."""

from collections.abc import Callable
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from modules.account_balance.adapters.outbound.repositories.sql.account_repository import (
    SqlAccountRepository,
)
from modules.account_balance.application.gateways.account_repository import (
    AccountNaturalKeyConflictError,
)
from modules.account_balance.domain.account import Account, AccountPurpose, AccountType
from modules.account_balance.domain.identifiers import AccountId, OwnerId
from modules.shared.domain.money import Currency

pytestmark = pytest.mark.integration

USD = Currency("USD")


def _open_user_account(
    *, owner_id: OwnerId, purpose: AccountPurpose = AccountPurpose.CHECKING
) -> Account:
    return Account.open(
        account_id=AccountId(uuid4()),
        owner_id=owner_id,
        account_type=AccountType.USER,
        purpose=purpose,
        currency=USD,
    )


async def test_add_then_find_by_natural_key_round_trips(
    session_factory: Callable[[], AsyncSession],
) -> None:
    repository = SqlAccountRepository(session_factory)
    owner_id = OwnerId(uuid4())
    account = _open_user_account(owner_id=owner_id)

    await repository.add(account)
    found = await repository.find_by_natural_key(
        owner_id=owner_id, purpose=AccountPurpose.CHECKING, currency=USD
    )

    assert found is not None
    assert found.account_id == account.account_id
    assert found.owner_id == owner_id
    assert found.balance.is_zero
    assert found.status.is_active()


async def test_find_by_natural_key_returns_none_when_absent(
    session_factory: Callable[[], AsyncSession],
) -> None:
    repository = SqlAccountRepository(session_factory)

    found = await repository.find_by_natural_key(
        owner_id=OwnerId(uuid4()), purpose=AccountPurpose.CHECKING, currency=USD
    )

    assert found is None


async def test_add_raises_conflict_on_duplicate_natural_key(
    session_factory: Callable[[], AsyncSession],
) -> None:
    repository = SqlAccountRepository(session_factory)
    owner_id = OwnerId(uuid4())
    first = _open_user_account(owner_id=owner_id)
    second = _open_user_account(owner_id=owner_id)

    await repository.add(first)
    with pytest.raises(AccountNaturalKeyConflictError):
        await repository.add(second)


async def test_get_by_account_id(session_factory: Callable[[], AsyncSession]) -> None:
    repository = SqlAccountRepository(session_factory)
    account = _open_user_account(owner_id=OwnerId(uuid4()))

    await repository.add(account)
    found = await repository.get(account.account_id)

    assert found is not None
    assert found.account_id == account.account_id


async def test_get_returns_none_when_absent(
    session_factory: Callable[[], AsyncSession],
) -> None:
    repository = SqlAccountRepository(session_factory)

    found = await repository.get(AccountId(uuid4()))

    assert found is None
