"""Unit tests for `ListAccounts` (docs/web-ui-plan.md §6.1b) against a fake in-memory repository.

`ListAccounts` is a one-line pass-through to `AccountRepository.find_by_owner` -- the port itself
(and its SQL adapter) owns the actual filtering/exclusion logic, covered separately
(`test_sql_account_repository.py`). This file exists to lock in that the use case asks for the
right owner and returns exactly what the port hands back.
"""

from uuid import uuid4

from modules.account_balance.application.gateways.account_repository import AccountRepository
from modules.account_balance.application.gateways.models.find_accounts_criteria import (
    FindAccountCriteria,
)
from modules.account_balance.application.use_cases.list_accounts import ListAccounts
from modules.account_balance.domain.account import Account, AccountPurpose, UserAccount
from modules.account_balance.domain.identifiers import AccountId, OwnerId
from modules.shared.domain.money import Currency

USD = Currency("USD")


class _FakeAccountRepository(AccountRepository):
    def __init__(self, by_owner: dict[OwnerId, tuple[UserAccount, ...]]) -> None:
        self._by_owner = by_owner
        self.last_requested_owner: OwnerId | None = None

    async def find(self, *, criteria: FindAccountCriteria) -> Account | None:
        raise NotImplementedError("ListAccounts never calls find()")

    async def add(self, account: Account) -> None:
        raise NotImplementedError("ListAccounts never adds an account")

    async def get_for_update(self, account_id: AccountId) -> UserAccount | None:
        raise NotImplementedError("ListAccounts never locks accounts")

    async def get_many_for_update(
        self, account_ids: tuple[AccountId, ...]
    ) -> tuple[UserAccount, ...]:
        raise NotImplementedError("ListAccounts never locks accounts")

    async def find_by_owner(self, owner_id: OwnerId) -> tuple[UserAccount, ...]:
        self.last_requested_owner = owner_id
        return self._by_owner.get(owner_id, ())

    async def update(self, account: UserAccount) -> None:
        raise NotImplementedError("ListAccounts never updates an account")


def _open_user(owner_id: OwnerId, purpose: AccountPurpose = AccountPurpose.CHECKING) -> UserAccount:
    return UserAccount.open(
        account_id=AccountId(uuid4()), owner_id=owner_id, purpose=purpose, currency=USD
    )


async def test_lists_every_account_the_port_returns_for_that_owner() -> None:
    owner_id = OwnerId(uuid4())
    checking = _open_user(owner_id, AccountPurpose.CHECKING)
    savings = _open_user(owner_id, AccountPurpose.SAVINGS)
    repository = _FakeAccountRepository({owner_id: (checking, savings)})
    use_case = ListAccounts(repository=repository)

    result = await use_case.execute(caller_id=owner_id)

    assert result == (checking, savings)
    assert repository.last_requested_owner == owner_id


async def test_an_owner_with_no_accounts_gets_an_empty_tuple() -> None:
    use_case = ListAccounts(repository=_FakeAccountRepository({}))

    result = await use_case.execute(caller_id=OwnerId(uuid4()))

    assert result == ()
