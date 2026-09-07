"""Example-based tests for the posting domain service (design §5.1, §5.2;
spec "Posting (domain service: transfer, revert)").

`TestTransfer` covers `transfer()`: the ordinary two-leg posting, the guards
that must refuse before any account is touched, and the atomicity regression
named in design §9.2. `TestRevert` is added in a later commit once `revert()`
exists (tasks.md T6.2).
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from modules.account_balance.domain.account import (
    Account,
    AccountPurpose,
    AccountStatus,
    AccountType,
)
from modules.account_balance.domain.entry import Entry, EntryDirection
from modules.account_balance.domain.errors import (
    AccountNotOperableError,
    InsufficientFundsError,
    SelfTransferError,
)
from modules.account_balance.domain.identifiers import (
    AccountId,
    EntryId,
    IdempotencyKey,
    OwnerId,
    TransferId,
)
from modules.account_balance.domain.posting import Posting, transfer
from modules.shared.domain.errors import CurrencyMismatchError
from modules.shared.domain.money import Currency, Money

USD = Currency("USD")
EUR = Currency("EUR")


def _open_account(
    *,
    account_id: AccountId | None = None,
    account_type: AccountType = AccountType.USER,
    purpose: AccountPurpose = AccountPurpose.CHECKING,
    currency: Currency = USD,
    balance: Money | None = None,
) -> Account:
    account = Account.open(
        account_id=account_id or AccountId(uuid4()),
        owner_id=OwnerId(uuid4()),
        account_type=account_type,
        purpose=purpose,
        currency=currency,
    )
    if balance is not None and balance.amount != 0:
        account = account.credit(
            Entry(
                entry_id=EntryId(uuid4()),
                transfer_id=TransferId(uuid4()),
                account_id=account.account_id,
                direction=EntryDirection.CREDIT,
                amount=balance,
                occurred_at=datetime.now(UTC),
            )
        )
    return account


def _post(
    *,
    source: Account,
    destination: Account,
    amount: Money,
    occurred_at: datetime | None = None,
) -> Posting:
    return transfer(
        transfer_id=TransferId(uuid4()),
        source=source,
        destination=destination,
        amount=amount,
        requested_by=OwnerId(uuid4()),
        idempotency_key=IdempotencyKey("posting-key"),
        occurred_at=occurred_at or datetime.now(UTC),
        entry_ids=(EntryId(uuid4()), EntryId(uuid4())),
    )


class TestTransfer:
    def test_posting_a_valid_transfer_moves_both_balances_and_records_both_legs(self) -> None:
        source = _open_account(balance=Money(200, USD))
        destination = _open_account(balance=Money.zero(USD))

        posting = _post(source=source, destination=destination, amount=Money(50, USD))

        balances = {account.account_id: account.balance for account in posting.accounts}
        assert balances[source.account_id] == Money(150, USD)
        assert balances[destination.account_id] == Money(50, USD)
        assert len(posting.transfer.entries) == 2
        net = sum((entry.signed_amount.amount for entry in posting.transfer.entries), start=0)
        assert net == 0

    def test_a_refused_debit_aborts_the_whole_posting(self) -> None:
        source = _open_account(balance=Money(10, USD))
        destination = _open_account(balance=Money.zero(USD))

        with pytest.raises(InsufficientFundsError):
            _post(source=source, destination=destination, amount=Money(50, USD))

        assert source.balance == Money(10, USD)
        assert destination.balance == Money.zero(USD)

    def test_cross_currency_transfer_is_rejected_before_posting(self) -> None:
        """spec "Cross-currency transfer is rejected before posting" (design
        §5.1 step 1, I4) — this is the check `Transfer.__post_init__` cannot
        make on its own (test_transfer.py's own note), checked against the
        `Account` objects before any `Entry` is built.
        """
        source = _open_account(currency=USD, balance=Money(100, USD))
        destination = _open_account(currency=EUR, balance=Money.zero(EUR))

        with pytest.raises(CurrencyMismatchError):
            _post(source=source, destination=destination, amount=Money(50, USD))

        assert source.balance == Money(100, USD)
        assert destination.balance == Money.zero(EUR)

    def test_self_transfer_is_rejected_before_any_account_is_touched(self) -> None:
        """design §5.1 step 1 — restates I5 at the service boundary."""
        account = _open_account(balance=Money(100, USD))

        with pytest.raises(SelfTransferError):
            _post(source=account, destination=account, amount=Money(50, USD))

        assert account.balance == Money(100, USD)

    def test_transfer_into_a_closed_destination_leaves_source_balance_unchanged(self) -> None:
        """design §9.2 "Atomicity" — a regression test for the §4.2 fix
        (mutate-then-validate): the destination's refusal must not have
        already touched the source.
        """
        source = _open_account(balance=Money(100, USD))
        destination = _open_account(balance=Money.zero(USD)).close()
        assert destination.status is AccountStatus.CLOSED

        with pytest.raises(AccountNotOperableError):
            _post(source=source, destination=destination, amount=Money(50, USD))

        assert source.balance == Money(100, USD)
