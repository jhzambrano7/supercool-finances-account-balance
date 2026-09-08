"""Unit tests for `ListMovements` (docs/web-ui-plan.md §6.2) against fake in-memory repositories.

Covers what belongs to the use case itself: resolving the account, the ownership check (and its
SYSTEM-account special case), and delegating pagination to `MovementRepository` unchanged. The
pagination/ordering logic itself belongs to the port's adapter, covered separately
(`test_sql_account_repository.py`'s sibling for movements would live beside the real SQL tests).
"""

from datetime import UTC, datetime
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
from modules.account_balance.application.gateways.models.movement import Movement, MovementPage
from modules.account_balance.application.gateways.movement_repository import MovementRepository
from modules.account_balance.application.use_cases.list_movements import ListMovements
from modules.account_balance.domain.account import (
    Account,
    AccountPurpose,
    SystemAccount,
    UserAccount,
)
from modules.account_balance.domain.entry import EntryDirection
from modules.account_balance.domain.errors import AccountOwnershipError
from modules.account_balance.domain.identifiers import (
    PLATFORM_OWNER_ID,
    AccountId,
    EntryId,
    OwnerId,
    TransferId,
)
from modules.shared.domain.money import Currency, Money

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
        raise NotImplementedError("ListMovements never adds an account")

    async def get_for_update(self, account_id: AccountId) -> UserAccount | None:
        raise NotImplementedError("ListMovements never locks accounts")

    async def get_many_for_update(
        self, account_ids: tuple[AccountId, ...]
    ) -> tuple[UserAccount, ...]:
        raise NotImplementedError("ListMovements never locks accounts")

    async def find_by_owner(self, owner_id: OwnerId) -> tuple[UserAccount, ...]:
        raise NotImplementedError("ListMovements never lists accounts by owner")

    async def update(self, account: UserAccount) -> None:
        raise NotImplementedError("ListMovements never updates an account")


class _FakeMovementRepository(MovementRepository):
    def __init__(self, page: MovementPage) -> None:
        self._page = page
        self.last_call: tuple[AccountId, int, str | None] | None = None

    async def find_by_account(
        self, *, account_id: AccountId, limit: int, cursor: str | None
    ) -> MovementPage:
        self.last_call = (account_id, limit, cursor)
        return self._page


def _open_user(owner_id: OwnerId) -> UserAccount:
    return UserAccount.open(
        account_id=AccountId(uuid4()),
        owner_id=owner_id,
        purpose=AccountPurpose.CHECKING,
        currency=USD,
    )


def _movement(account_id: AccountId) -> Movement:
    return Movement(
        entry_id=EntryId(uuid4()),
        transfer_id=TransferId(uuid4()),
        direction=EntryDirection.CREDIT,
        amount=Money(1_000, USD),
        counterparty_account_id=account_id,
        requested_by=OwnerId(uuid4()),
        occurred_at=datetime(2026, 9, 7, tzinfo=UTC),
    )


async def test_the_owner_gets_the_page_the_port_returns() -> None:
    owner_id = OwnerId(uuid4())
    account = _open_user(owner_id)
    page = MovementPage(items=(_movement(account.account_id),), next_cursor="opaque")
    movement_repository = _FakeMovementRepository(page)
    use_case = ListMovements(
        account_repository=_FakeAccountRepository({account.account_id: account}),
        movement_repository=movement_repository,
    )

    result = await use_case.execute(
        account_id=account.account_id, caller_id=owner_id, limit=25, cursor=None
    )

    assert result is page
    assert movement_repository.last_call == (account.account_id, 25, None)


async def test_a_stranger_is_refused_before_the_movement_repository_is_asked() -> None:
    owner_id = OwnerId(uuid4())
    stranger_id = OwnerId(uuid4())
    account = _open_user(owner_id)
    movement_repository = _FakeMovementRepository(MovementPage(items=(), next_cursor=None))
    use_case = ListMovements(
        account_repository=_FakeAccountRepository({account.account_id: account}),
        movement_repository=movement_repository,
    )

    with pytest.raises(AccountOwnershipError):
        await use_case.execute(
            account_id=account.account_id, caller_id=stranger_id, limit=25, cursor=None
        )

    assert movement_repository.last_call is None


async def test_a_missing_account_is_reported_as_not_found() -> None:
    use_case = ListMovements(
        account_repository=_FakeAccountRepository({}),
        movement_repository=_FakeMovementRepository(MovementPage(items=(), next_cursor=None)),
    )

    with pytest.raises(AccountNotFoundError):
        await use_case.execute(
            account_id=AccountId(uuid4()), caller_id=OwnerId(uuid4()), limit=25, cursor=None
        )


async def test_a_system_account_is_refused_like_a_strangers_account() -> None:
    """SYSTEM movement history is an operator concern, not requested here (docs/web-ui-plan.md
    §6.2) -- it fails the same ownership check a stranger's account would."""
    system_account = SystemAccount(
        account_id=AccountId(uuid4()),
        owner_id=PLATFORM_OWNER_ID,
        purpose=AccountPurpose.FUNDING,
        currency=USD,
    )
    use_case = ListMovements(
        account_repository=_FakeAccountRepository({system_account.account_id: system_account}),
        movement_repository=_FakeMovementRepository(MovementPage(items=(), next_cursor=None)),
    )

    with pytest.raises(AccountOwnershipError):
        await use_case.execute(
            account_id=system_account.account_id,
            caller_id=OwnerId(uuid4()),
            limit=25,
            cursor=None,
        )
