"""Property-based test for `transfer()`'s conservation invariant (design §9.1 P1).

For any two accounts and any amount the source can cover, `transfer()` must
leave `source.balance + destination.balance` unchanged — this is the D2 sign
convention made observable: a flipped sign in `signed_amount` would fail this
immediately.
"""

from datetime import UTC, datetime
from uuid import uuid4

from hypothesis import given
from hypothesis import strategies as st

from modules.account_balance.domain.account import AccountPurpose, UserAccount
from modules.account_balance.domain.entry import Entry, EntryDirection
from modules.account_balance.domain.identifiers import (
    AccountId,
    EntryId,
    IdempotencyKey,
    OwnerId,
    TransferId,
)
from modules.account_balance.domain.posting import transfer
from modules.shared.domain.money import Currency, Money

USD = Currency("USD")


def _account_with_balance(balance: Money) -> UserAccount:
    account = UserAccount.open(
        account_id=AccountId(uuid4()),
        owner_id=OwnerId(uuid4()),
        purpose=AccountPurpose.CHECKING,
        currency=USD,
    )
    if balance.amount == 0:
        return account
    return account.credit(
        Entry(
            entry_id=EntryId(uuid4()),
            transfer_id=TransferId(uuid4()),
            account_id=account.account_id,
            direction=EntryDirection.CREDIT,
            amount=balance,
            occurred_at=datetime.now(UTC),
        )
    )


class TestPropertyP1ConservationAcrossTransfer:
    """P1: `transfer()` never creates or destroys money, only relocates it.

    `amount` is generated as `<= source's starting balance` by construction
    (`source_extra` adds any non-negative headroom on top), so every posting
    in this test is always affordable and none is refused by I2 — the
    property under test is conservation, not overdraft refusal (that is P3's
    job, in `test_account_properties.py`).
    """

    @given(
        amount=st.integers(min_value=1, max_value=10_000),
        source_extra=st.integers(min_value=0, max_value=10_000),
        destination_balance=st.integers(min_value=0, max_value=10_000),
    )
    def test_transfer_conserves_the_sum_of_both_balances(
        self, amount: int, source_extra: int, destination_balance: int
    ) -> None:
        source = _account_with_balance(Money(amount + source_extra, USD))
        destination = _account_with_balance(Money(destination_balance, USD))
        before = source.balance.amount + destination.balance.amount

        posting = transfer(
            transfer_id=TransferId(uuid4()),
            source=source,
            destination=destination,
            amount=Money(amount, USD),
            requested_by=OwnerId(uuid4()),
            idempotency_key=IdempotencyKey("prop-key"),
            occurred_at=datetime.now(UTC),
            entry_ids=lambda: EntryId(uuid4()),
        )

        after = sum(account.balance.amount for account in posting.accounts)
        assert after == before
