"""Unit tests for `GetAccount` (docs/web-ui-plan.md §6.1) against a fake in-memory repository.

Covers: happy path (owner reads their own account), ownership refusal (a stranger's account),
not-found, and the SYSTEM-account case -- both the ordinary path (a stranger's `caller_id` fails
the ownership check outright) and the one that actually exercises the `isinstance` guard (a
caller who spoofs `X-Caller-Id` to `PLATFORM_OWNER_ID` itself, passing the ownership check on
identity alone -- `X-Caller-Id` is a bare, unvalidated UUID header, T2).
"""

import logging
from uuid import uuid4

import pytest

from modules.account_balance.application.gateways.account_repository import (
    AccountNotFoundError,
    AccountRepository,
)
from modules.account_balance.application.gateways.models.find_accounts_criteria import (
    FindAccountByAccountId,
    FindAccountCriteria,
)
from modules.account_balance.application.use_cases.get_account import GetAccount
from modules.account_balance.domain.account import (
    Account,
    AccountPurpose,
    SystemAccount,
    UserAccount,
)
from modules.account_balance.domain.errors import AccountOwnershipError
from modules.account_balance.domain.identifiers import PLATFORM_OWNER_ID, AccountId, OwnerId
from modules.shared.domain.money import Currency

USD = Currency("USD")


class _FakeAccountRepository(AccountRepository):
    def __init__(self, accounts: dict[AccountId, Account]) -> None:
        self._accounts = accounts

    async def find(self, *, criteria: FindAccountCriteria) -> Account | None:
        match criteria:
            case FindAccountByAccountId(account_id):
                return self._accounts.get(account_id)
            case _:
                raise NotImplementedError

    async def add(self, account: Account) -> None:
        raise NotImplementedError("GetAccount never adds an account")

    async def get_for_update(self, account_id: AccountId) -> UserAccount | None:
        raise NotImplementedError("GetAccount never locks accounts")

    async def get_many_for_update(
        self, account_ids: tuple[AccountId, ...]
    ) -> tuple[UserAccount, ...]:
        raise NotImplementedError("GetAccount never locks accounts")

    async def find_by_owner(self, owner_id: OwnerId) -> tuple[UserAccount, ...]:
        raise NotImplementedError("GetAccount never lists accounts by owner")

    async def update(self, account: UserAccount) -> None:
        raise NotImplementedError("GetAccount never updates an account")


def _open_user(owner_id: OwnerId) -> UserAccount:
    return UserAccount.open(
        account_id=AccountId(uuid4()),
        owner_id=owner_id,
        purpose=AccountPurpose.CHECKING,
        currency=USD,
    )


async def test_the_owner_can_read_their_own_account() -> None:
    owner_id = OwnerId(uuid4())
    account = _open_user(owner_id)
    use_case = GetAccount(
        repository=_FakeAccountRepository({account.account_id: account}),
        logger=logging.getLogger(__name__),
    )

    result = await use_case.execute(account_id=account.account_id, caller_id=owner_id)

    assert result is account


async def test_a_stranger_is_refused_with_account_ownership_error() -> None:
    owner_id = OwnerId(uuid4())
    stranger_id = OwnerId(uuid4())
    account = _open_user(owner_id)
    use_case = GetAccount(
        repository=_FakeAccountRepository({account.account_id: account}),
        logger=logging.getLogger(__name__),
    )

    with pytest.raises(AccountOwnershipError):
        await use_case.execute(account_id=account.account_id, caller_id=stranger_id)


async def test_a_missing_account_is_reported_as_not_found() -> None:
    use_case = GetAccount(repository=_FakeAccountRepository({}), logger=logging.getLogger(__name__))

    with pytest.raises(AccountNotFoundError):
        await use_case.execute(account_id=AccountId(uuid4()), caller_id=OwnerId(uuid4()))


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
    use_case = GetAccount(
        repository=_FakeAccountRepository({system_account.account_id: system_account}),
        logger=logging.getLogger(__name__),
    )

    with pytest.raises(AccountOwnershipError):
        await use_case.execute(account_id=system_account.account_id, caller_id=OwnerId(uuid4()))


async def test_a_spoofed_platform_owner_id_is_still_refused() -> None:
    """T7: a SYSTEM account has no balance -- this must never reach `AccountResponseDto`, not even
    if a caller spoofs `X-Caller-Id` to `PLATFORM_OWNER_ID` itself (a bare, unvalidated UUID
    header, T2) and so passes the ownership check on identity alone. The `isinstance` guard is
    what actually stops this, not the ownership check -- this is the case that exercises it."""
    system_account = _funding_account()
    use_case = GetAccount(
        repository=_FakeAccountRepository({system_account.account_id: system_account}),
        logger=logging.getLogger(__name__),
    )

    with pytest.raises(AccountOwnershipError):
        await use_case.execute(account_id=system_account.account_id, caller_id=PLATFORM_OWNER_ID)
