"""Integration tests for `SqlAccountRepository` against a real PostgreSQL."""

import logging
from collections.abc import Callable
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from modules.account_balance.adapters.outbound.repositories.sql.sql_account_repository import (
    SqlAccountRepository,
)
from modules.account_balance.application.gateways.account_repository import (
    AccountAlreadyExistsError,
    AccountNotFoundError,
)
from modules.account_balance.application.gateways.models.find_accounts_criteria import (
    FindAccountByAccountId,
    FindAccountByOwnerAndPurposeAndCurrency,
    FindSystemAccountByPurposeAndCurrency,
)
from modules.account_balance.domain.account import AccountPurpose, SystemAccount, UserAccount
from modules.account_balance.domain.identifiers import PLATFORM_OWNER_ID, AccountId, OwnerId
from modules.shared.domain.money import Currency

pytestmark = pytest.mark.integration

USD = Currency("USD")
_logger = logging.getLogger(__name__)


def _repository(session_factory: Callable[[], AsyncSession]) -> SqlAccountRepository:
    return SqlAccountRepository(_logger, session_factory)


def _open_user_account(
    *, owner_id: OwnerId, purpose: AccountPurpose = AccountPurpose.CHECKING
) -> UserAccount:
    return UserAccount.open(
        account_id=AccountId(uuid4()),
        owner_id=owner_id,
        purpose=purpose,
        currency=USD,
    )


async def test_add_then_find_by_natural_key_criteria_round_trips(
    session_factory: Callable[[], AsyncSession],
) -> None:
    repository = _repository(session_factory)
    owner_id = OwnerId(uuid4())
    account = _open_user_account(owner_id=owner_id)

    await repository.add(account)
    found = await repository.find(
        criteria=FindAccountByOwnerAndPurposeAndCurrency(
            owner_id=owner_id, purpose=AccountPurpose.CHECKING, currency=USD
        )
    )

    assert isinstance(found, UserAccount)
    assert found.account_id == account.account_id
    assert found.owner_id == owner_id
    assert found.balance.is_zero
    assert found.status.is_active()


async def test_find_by_natural_key_criteria_returns_none_when_absent(
    session_factory: Callable[[], AsyncSession],
) -> None:
    repository = _repository(session_factory)

    found = await repository.find(
        criteria=FindAccountByOwnerAndPurposeAndCurrency(
            owner_id=OwnerId(uuid4()), purpose=AccountPurpose.CHECKING, currency=USD
        )
    )

    assert found is None


async def test_add_raises_conflict_on_duplicate_natural_key(
    session_factory: Callable[[], AsyncSession],
) -> None:
    repository = _repository(session_factory)
    owner_id = OwnerId(uuid4())
    first = _open_user_account(owner_id=owner_id)
    second = _open_user_account(owner_id=owner_id)

    await repository.add(first)
    with pytest.raises(AccountAlreadyExistsError) as exc_info:
        await repository.add(second)

    # Structured fields, not just a message string a caller would have to
    # parse -- the point of AccountAlreadyExistsError over a bare Exception.
    error = exc_info.value
    assert error.owner_id == owner_id
    assert error.purpose is AccountPurpose.CHECKING
    assert error.currency == USD
    assert error.resource_type == "account"


async def test_find_by_account_id_round_trips(session_factory: Callable[[], AsyncSession]) -> None:
    repository = _repository(session_factory)
    account = _open_user_account(owner_id=OwnerId(uuid4()))

    await repository.add(account)
    found = await repository.find(criteria=FindAccountByAccountId(account.account_id))

    assert found is not None
    assert found.account_id == account.account_id


async def test_find_by_account_id_returns_none_when_absent(
    session_factory: Callable[[], AsyncSession],
) -> None:
    repository = _repository(session_factory)

    found = await repository.find(criteria=FindAccountByAccountId(AccountId(uuid4())))

    assert found is None


async def test_get_by_account_id_round_trips(session_factory: Callable[[], AsyncSession]) -> None:
    repository = _repository(session_factory)
    account = _open_user_account(owner_id=OwnerId(uuid4()))

    await repository.add(account)
    found = await repository.get(criteria=FindAccountByAccountId(account.account_id))

    assert found.account_id == account.account_id


async def test_get_raises_not_found_when_absent(
    session_factory: Callable[[], AsyncSession],
) -> None:
    """`get()` is find-or-raise (AccountRepository's own base implementation),
    unlike `find()`'s find-or-`None` -- the two exist side by side precisely
    so a caller picks the failure mode it wants instead of re-deriving
    "not found" from a `None` check at every call site.
    """
    repository = _repository(session_factory)
    criteria = FindAccountByAccountId(AccountId(uuid4()))

    with pytest.raises(AccountNotFoundError) as exc_info:
        await repository.get(criteria=criteria)

    assert exc_info.value.criteria == criteria
    assert exc_info.value.resource_type == "account"


async def test_find_system_account_by_purpose_and_currency_finds_the_seeded_funding_account(
    session_factory: Callable[[], AsyncSession],
) -> None:
    """T8: migration `5bf582a92358` seeds exactly one `FUNDING` and one `SETTLEMENT` account in
    USD -- this is the query `Deposit`/`Withdraw` run against a real database."""
    repository = _repository(session_factory)

    found = await repository.find(
        criteria=FindSystemAccountByPurposeAndCurrency(purpose=AccountPurpose.FUNDING, currency=USD)
    )

    assert isinstance(found, SystemAccount)
    assert found.purpose is AccountPurpose.FUNDING
    assert found.currency == USD


async def test_find_system_account_by_purpose_and_currency_returns_none_for_an_unseeded_currency(
    session_factory: Callable[[], AsyncSession],
) -> None:
    """No `EUR` `FUNDING`/`SETTLEMENT` account is seeded -- exactly the gap
    `CurrencyNotOperationalError` exists to report, one layer up."""
    repository = _repository(session_factory)

    found = await repository.find(
        criteria=FindSystemAccountByPurposeAndCurrency(
            purpose=AccountPurpose.FUNDING, currency=Currency("EUR")
        )
    )

    assert found is None


async def test_find_by_owner_returns_every_account_that_owner_holds(
    session_factory: Callable[[], AsyncSession],
) -> None:
    repository = _repository(session_factory)
    owner_id = OwnerId(uuid4())
    checking = _open_user_account(owner_id=owner_id, purpose=AccountPurpose.CHECKING)
    savings = _open_user_account(owner_id=owner_id, purpose=AccountPurpose.SAVINGS)
    someone_else = _open_user_account(owner_id=OwnerId(uuid4()))
    await repository.add(checking)
    await repository.add(savings)
    await repository.add(someone_else)

    found = await repository.find_by_owner(owner_id)

    assert {account.account_id for account in found} == {checking.account_id, savings.account_id}
    assert all(isinstance(account, UserAccount) for account in found)


async def test_find_by_owner_never_returns_a_system_account(
    session_factory: Callable[[], AsyncSession],
) -> None:
    """T6/T7: `PLATFORM_OWNER_ID` is never a real caller's `owner_id`, but the query excludes
    `SYSTEM` rows itself regardless -- this asserts that exclusion directly, against the
    migration's own seeded `FUNDING`/`SETTLEMENT` accounts (T8)."""
    repository = _repository(session_factory)

    found = await repository.find_by_owner(PLATFORM_OWNER_ID)

    assert found == ()


async def test_find_by_owner_returns_empty_for_an_owner_with_no_accounts(
    session_factory: Callable[[], AsyncSession],
) -> None:
    repository = _repository(session_factory)

    found = await repository.find_by_owner(OwnerId(uuid4()))

    assert found == ()
