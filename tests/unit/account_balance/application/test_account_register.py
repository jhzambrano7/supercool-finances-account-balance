"""Unit tests for `AccountRegister` against a fake in-memory repository.

Per openspec/specs/account-opening/spec.md's Testing Strategy: first-open,
existing-natural-key return, and the unique-violation-race path (the fake
raises the same conflict the SQL adapter would, AO4) -- now propagated to
the caller rather than recovered inside the use case.

Why a hand-written fake, not per-test `unittest.mock` objects: the natural-
key uniqueness and the AO4 race are *stateful* behavior shared across every
test below. A `Mock`/`AsyncMock` would still need that same state wired up
via `side_effect` in each test, just duplicated instead of centralized once
here -- and it wouldn't fail mypy if `AccountRepository`'s signature drifted,
since fakes are checked as real subclasses of the port they implement.
"""

from uuid import uuid4

import pytest

from modules.account_balance.application.gateways.account_repository import (
    AccountAlreadyExistsError,
    AccountRepository,
)
from modules.account_balance.application.gateways.models.find_accounts_criteria import (
    FindAccountByAccountId,
    FindAccountByOwnerAndPurposeAndCurrency,
    FindAccountCriteria,
)
from modules.account_balance.application.use_cases.account_register import AccountRegister
from modules.account_balance.domain.account import Account, AccountPurpose, AccountType
from modules.account_balance.domain.errors import InvalidAccountPurposeError
from modules.account_balance.domain.identifiers import OwnerId
from modules.shared.application.services.id_generator import IdGenerator
from modules.shared.domain.money import Currency

USD = Currency("USD")


class _FakeAccountRepository(AccountRepository):
    """In-memory stand-in keyed by natural key.

    `find` is the only abstract method this port requires (`get` -- find-or-
    raise -- comes for free from the base class once `find` is implemented).
    `force_conflict_once` simulates AO4's race: the next `add()` behaves as
    if a concurrent request already committed the row, raising the same
    conflict the SQL adapter raises on a unique-violation.
    """

    def __init__(self) -> None:
        self.by_natural_key: dict[tuple[OwnerId, AccountPurpose, Currency], Account] = {}
        self.force_conflict_once = False

    async def find(self, *, criteria: FindAccountCriteria) -> Account | None:
        match criteria:
            case FindAccountByOwnerAndPurposeAndCurrency(owner_id, purpose, currency):
                return self.by_natural_key.get((owner_id, purpose, currency))
            case FindAccountByAccountId(account_id):
                return next(
                    (a for a in self.by_natural_key.values() if a.account_id == account_id), None
                )

    async def add(self, account: Account) -> None:
        key = (account.owner_id, account.purpose, account.currency)
        if self.force_conflict_once:
            self.force_conflict_once = False
            self.by_natural_key[key] = account
            raise AccountAlreadyExistsError(
                owner_id=account.owner_id, purpose=account.purpose, currency=account.currency
            )
        if key in self.by_natural_key:
            raise AccountAlreadyExistsError(
                owner_id=account.owner_id, purpose=account.purpose, currency=account.currency
            )
        self.by_natural_key[key] = account


def _use_case(repository: AccountRepository) -> AccountRegister:
    return AccountRegister(repository=repository, id_generator=IdGenerator())


async def test_first_open_creates_a_new_account() -> None:
    repository = _FakeAccountRepository()
    use_case = _use_case(repository)
    owner_id = OwnerId(uuid4())

    result = await use_case.execute(
        owner_id=owner_id, purpose=AccountPurpose.CHECKING, currency=USD
    )

    assert result.created is True
    assert result.account.owner_id == owner_id
    assert result.account.account_type is AccountType.USER
    assert result.account.purpose is AccountPurpose.CHECKING
    assert result.account.balance.is_zero
    stored = await repository.find(
        criteria=FindAccountByOwnerAndPurposeAndCurrency(
            owner_id=owner_id, purpose=AccountPurpose.CHECKING, currency=USD
        )
    )
    assert stored == result.account


async def test_retrying_an_open_returns_the_existing_account() -> None:
    repository = _FakeAccountRepository()
    use_case = _use_case(repository)
    owner_id = OwnerId(uuid4())

    first = await use_case.execute(owner_id=owner_id, purpose=AccountPurpose.CHECKING, currency=USD)
    second = await use_case.execute(
        owner_id=owner_id, purpose=AccountPurpose.CHECKING, currency=USD
    )

    assert first.created is True
    assert second.created is False
    assert second.account.account_id == first.account.account_id
    assert len(repository.by_natural_key) == 1


async def test_a_losing_concurrent_insert_propagates_the_conflict() -> None:
    """AO4's race, found by a genuinely concurrent insert: propagated to the
    caller, not recovered here (reviewer's own call — the client's retry
    finds the account via the same natural-key lookup this use case already
    tries first, so a second recovery path inside this method is redundant).
    """
    repository = _FakeAccountRepository()
    repository.force_conflict_once = True
    use_case = _use_case(repository)
    owner_id = OwnerId(uuid4())

    with pytest.raises(AccountAlreadyExistsError) as exc_info:
        await use_case.execute(owner_id=owner_id, purpose=AccountPurpose.CHECKING, currency=USD)

    assert exc_info.value.owner_id == owner_id
    assert exc_info.value.purpose is AccountPurpose.CHECKING
    assert exc_info.value.currency == USD


async def test_an_invalid_purpose_is_rejected_before_any_persistence() -> None:
    repository = _FakeAccountRepository()
    use_case = _use_case(repository)

    with pytest.raises(InvalidAccountPurposeError):
        await use_case.execute(
            owner_id=OwnerId(uuid4()), purpose=AccountPurpose.FUNDING, currency=USD
        )

    assert repository.by_natural_key == {}
