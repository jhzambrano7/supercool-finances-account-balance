"""Unit tests for the DBO <-> domain mapping (point 3 of the adapter
conventions: a DBO's own mapping logic gets a unit test, not only indirect
coverage through an integration test hitting a real database).
"""

from uuid import uuid4

from modules.account_balance.adapters.outbound.repositories.sql.dbos.models import AccountDbo
from modules.account_balance.domain.account import (
    Account,
    AccountPurpose,
    AccountStatus,
    AccountType,
)
from modules.account_balance.domain.identifiers import AccountId, OwnerId
from modules.shared.domain.money import Currency, Money

USD = Currency("USD")


def test_from_domain_then_as_domain_round_trips_a_freshly_opened_account() -> None:
    account = Account.open(
        account_id=AccountId(uuid4()),
        owner_id=OwnerId(uuid4()),
        account_type=AccountType.USER,
        purpose=AccountPurpose.CHECKING,
        currency=USD,
    )

    dbo = AccountDbo.from_domain(account)

    assert dbo.account_id == account.account_id.value
    assert dbo.owner_id == account.owner_id.value
    assert dbo.account_type == AccountType.USER.value
    assert dbo.purpose == AccountPurpose.CHECKING.value
    assert dbo.currency == "USD"
    assert dbo.balance_amount == 0
    assert dbo.status == AccountStatus.ACTIVE.value
    assert dbo.version == 0
    assert dbo.as_domain() == account


def test_as_domain_reconstitutes_a_non_zero_balance_without_re_asserting_it() -> None:
    """A row can legitimately hold a negative `USER` balance (a reversal's
    clawback, PRD §7.3) -- `as_domain()` must use `reconstitute()`, never
    `open()`, or loading such a row would itself raise.
    """
    account_id = AccountId(uuid4())
    owner_id = OwnerId(uuid4())
    dbo = AccountDbo(
        account_id=account_id.value,
        owner_id=owner_id.value,
        account_type=AccountType.USER.value,
        purpose=AccountPurpose.CHECKING.value,
        currency="USD",
        balance_amount=-8000,
        status=AccountStatus.ACTIVE.value,
        version=3,
    )

    account = dbo.as_domain()

    assert account.account_id == account_id
    assert account.owner_id == owner_id
    assert account.balance == Money(-8000, USD)
    assert account.version == 3
