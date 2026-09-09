"""Unit tests for `CloseAccount` (docs/prd.md §7.2) against a fake in-memory unit of work.

Covers: happy path, the two domain refusals (`AccountNotEmptyError`, `AccountNotClosableError`),
ownership, not-found, and the same SYSTEM-account-by-spoofed-caller case `GetAccount`/
`ListMovements` are tested against -- the merged ownership/isinstance condition in `CloseAccount`
mirrors theirs exactly, so it gets the same two-part coverage (a stranger fails the ordinary
check; a caller who spoofs `X-Caller-Id` to `PLATFORM_OWNER_ID` is what actually exercises the
`isinstance` guard).
"""

import logging
from datetime import UTC, datetime
from typing import Self
from uuid import uuid4

import pytest

from modules.account_balance.application.gateways.account_repository import (
    AccountNotFoundError,
    AccountRepository,
)
from modules.account_balance.application.gateways.account_unit_of_work import AccountUnitOfWork
from modules.account_balance.application.gateways.models.find_accounts_criteria import (
    FindAccountByAccountId,
    FindAccountCriteria,
)
from modules.account_balance.application.use_cases.close_account import (
    CloseAccount,
    CloseAccountRequest,
)
from modules.account_balance.domain.account import (
    Account,
    AccountPurpose,
    AccountStatus,
    SystemAccount,
    UserAccount,
)
from modules.account_balance.domain.entry import Entry, EntryDirection
from modules.account_balance.domain.errors import (
    AccountNotClosableError,
    AccountNotEmptyError,
    AccountOwnershipError,
)
from modules.account_balance.domain.identifiers import (
    PLATFORM_OWNER_ID,
    AccountId,
    EntryId,
    OwnerId,
    TransferId,
)
from modules.shared.domain.money import Currency, Money

USD = Currency("USD")


class _Database:
    def __init__(self) -> None:
        self.accounts: dict[AccountId, Account] = {}


class _FakeAccountRepository(AccountRepository):
    def __init__(self, database: _Database) -> None:
        self._database = database

    async def find(self, *, criteria: FindAccountCriteria) -> Account | None:
        match criteria:
            case FindAccountByAccountId(account_id):
                return self._database.accounts.get(account_id)
            case _:
                raise NotImplementedError

    async def add(self, account: Account) -> None:
        raise NotImplementedError("CloseAccount never adds an account")

    async def get_for_update(self, account_id: AccountId) -> UserAccount | None:
        account = self._database.accounts.get(account_id)
        return account if isinstance(account, UserAccount) else None

    async def get_many_for_update(
        self, account_ids: tuple[AccountId, ...]
    ) -> tuple[UserAccount, ...]:
        raise NotImplementedError("CloseAccount locks exactly one account")

    async def find_by_owner(self, owner_id: OwnerId) -> tuple[UserAccount, ...]:
        raise NotImplementedError("CloseAccount never lists accounts by owner")

    async def update(self, account: UserAccount) -> None:
        self._database.accounts[account.account_id] = account


class _FakeUnitOfWork(AccountUnitOfWork):
    def __init__(self, database: _Database) -> None:
        self._database = database

    async def __aenter__(self) -> Self:
        self.accounts = _FakeAccountRepository(self._database)
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None


def _close_account(database: _Database) -> CloseAccount:
    return CloseAccount(
        logger=logging.getLogger(__name__), unit_of_work_factory=lambda: _FakeUnitOfWork(database)
    )


def _open_user(owner_id: OwnerId) -> UserAccount:
    return UserAccount.open(
        account_id=AccountId(uuid4()),
        owner_id=owner_id,
        purpose=AccountPurpose.CHECKING,
        currency=USD,
    )


async def test_a_zero_balance_active_account_closes() -> None:
    owner_id = OwnerId(uuid4())
    account = _open_user(owner_id)
    database = _Database()
    database.accounts[account.account_id] = account

    result = await _close_account(database).execute(
        CloseAccountRequest(account_id=account.account_id, caller_id=owner_id)
    )

    assert result.status is AccountStatus.CLOSED
    persisted = database.accounts[account.account_id]
    assert isinstance(persisted, UserAccount)
    assert persisted.status is AccountStatus.CLOSED


async def test_a_non_zero_balance_is_refused() -> None:
    owner_id = OwnerId(uuid4())
    opened = _open_user(owner_id)
    account = opened.credit(
        Entry(
            entry_id=EntryId(uuid4()),
            transfer_id=TransferId(uuid4()),
            account_id=opened.account_id,
            direction=EntryDirection.CREDIT,
            amount=Money(100, USD),
            occurred_at=datetime(2026, 9, 8, tzinfo=UTC),
        )
    )
    database = _Database()
    database.accounts[account.account_id] = account

    with pytest.raises(AccountNotEmptyError):
        await _close_account(database).execute(
            CloseAccountRequest(account_id=account.account_id, caller_id=owner_id)
        )
    persisted = database.accounts[account.account_id]
    assert isinstance(persisted, UserAccount)
    assert persisted.status is AccountStatus.ACTIVE


async def test_an_already_closed_account_is_refused() -> None:
    owner_id = OwnerId(uuid4())
    account = _open_user(owner_id).close()
    database = _Database()
    database.accounts[account.account_id] = account

    with pytest.raises(AccountNotClosableError):
        await _close_account(database).execute(
            CloseAccountRequest(account_id=account.account_id, caller_id=owner_id)
        )


async def test_a_stranger_is_refused_with_account_ownership_error() -> None:
    owner_id = OwnerId(uuid4())
    stranger_id = OwnerId(uuid4())
    account = _open_user(owner_id)
    database = _Database()
    database.accounts[account.account_id] = account

    with pytest.raises(AccountOwnershipError):
        await _close_account(database).execute(
            CloseAccountRequest(account_id=account.account_id, caller_id=stranger_id)
        )


async def test_a_missing_account_is_reported_as_not_found() -> None:
    database = _Database()

    with pytest.raises(AccountNotFoundError):
        await _close_account(database).execute(
            CloseAccountRequest(account_id=AccountId(uuid4()), caller_id=OwnerId(uuid4()))
        )


def _funding_account() -> SystemAccount:
    return SystemAccount(
        account_id=AccountId(uuid4()),
        owner_id=PLATFORM_OWNER_ID,
        purpose=AccountPurpose.FUNDING,
        currency=USD,
    )


async def test_a_system_account_is_refused_like_a_strangers_account() -> None:
    """A caller unrelated to the platform fails the ordinary ownership check -- `PLATFORM_OWNER_ID`
    never equals a real caller's id, so this path never reaches the `isinstance` check at all."""
    system_account = _funding_account()
    database = _Database()
    database.accounts[system_account.account_id] = system_account

    with pytest.raises(AccountOwnershipError):
        await _close_account(database).execute(
            CloseAccountRequest(account_id=system_account.account_id, caller_id=OwnerId(uuid4()))
        )


async def test_a_spoofed_platform_owner_id_is_still_refused() -> None:
    """A caller who spoofs `X-Caller-Id` to `PLATFORM_OWNER_ID` itself passes the ownership check
    on identity alone -- the `isinstance` guard is what actually stops a SYSTEM account from ever
    reaching `Account.close()` (which does not exist on `SystemAccount` in the first place), not
    the ownership check. This is the case that exercises it."""
    system_account = _funding_account()
    database = _Database()
    database.accounts[system_account.account_id] = system_account

    with pytest.raises(AccountOwnershipError):
        await _close_account(database).execute(
            CloseAccountRequest(account_id=system_account.account_id, caller_id=PLATFORM_OWNER_ID)
        )
