"""Unit tests for `GetAccount` (docs/web-ui-plan.md §6.1) against a fake in-memory repository.

Covers: happy path (owner reads their own account), ownership refusal (a stranger's account),
not-found, and the SYSTEM-account case -- which fails ownership rather than needing its own check,
since `PLATFORM_OWNER_ID` never equals a real caller's id.
"""

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
    use_case = GetAccount(repository=_FakeAccountRepository({account.account_id: account}))

    result = await use_case.execute(account_id=account.account_id, caller_id=owner_id)

    assert result is account


async def test_a_stranger_is_refused_with_account_ownership_error() -> None:
    owner_id = OwnerId(uuid4())
    stranger_id = OwnerId(uuid4())
    account = _open_user(owner_id)
    use_case = GetAccount(repository=_FakeAccountRepository({account.account_id: account}))

    with pytest.raises(AccountOwnershipError):
        await use_case.execute(account_id=account.account_id, caller_id=stranger_id)


async def test_a_missing_account_is_reported_as_not_found() -> None:
    use_case = GetAccount(repository=_FakeAccountRepository({}))

    with pytest.raises(AccountNotFoundError):
        await use_case.execute(account_id=AccountId(uuid4()), caller_id=OwnerId(uuid4()))


async def test_a_system_account_is_refused_like_a_strangers_account() -> None:
    """T7: a SYSTEM account has no balance -- this must never reach `AccountResponseDto`. It
    fails the same ownership check a stranger's account would, since its owner is
    `PLATFORM_OWNER_ID`, never a real caller's id -- no SYSTEM-specific branch needed."""
    system_account = SystemAccount(
        account_id=AccountId(uuid4()),
        owner_id=PLATFORM_OWNER_ID,
        purpose=AccountPurpose.FUNDING,
        currency=USD,
    )
    use_case = GetAccount(
        repository=_FakeAccountRepository({system_account.account_id: system_account})
    )

    with pytest.raises(AccountOwnershipError):
        await use_case.execute(account_id=system_account.account_id, caller_id=OwnerId(uuid4()))
