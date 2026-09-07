from datetime import UTC, datetime
from uuid import uuid4

import pytest

from modules.account_balance.domain.entry import Entry, EntryDirection
from modules.account_balance.domain.errors import NaiveTimestampError, NonPositiveAmountError
from modules.account_balance.domain.identifiers import AccountId, EntryId, TransferId
from modules.shared.domain.money import Currency, Money

USD = Currency("USD")


def _make_entry(*, amount: Money, direction: EntryDirection, occurred_at: datetime) -> Entry:
    return Entry(
        entry_id=EntryId(uuid4()),
        transfer_id=TransferId(uuid4()),
        account_id=AccountId(uuid4()),
        direction=direction,
        amount=amount,
        occurred_at=occurred_at,
    )


class TestEntryIsAStrictlyPositiveMovement:
    def test_zero_amount_entry_is_rejected(self) -> None:
        with pytest.raises(NonPositiveAmountError):
            _make_entry(
                amount=Money(0, USD), direction=EntryDirection.DEBIT, occurred_at=datetime.now(UTC)
            )

    def test_negative_amount_entry_is_rejected(self) -> None:
        """I3: not in the spec's own scenario list verbatim, but an obvious extension of "strictly
        positive" — a negative amount is even less representable than zero."""
        with pytest.raises(NonPositiveAmountError):
            _make_entry(
                amount=Money(-100, USD),
                direction=EntryDirection.DEBIT,
                occurred_at=datetime.now(UTC),
            )


class TestSignedAmount:
    def test_signed_amount_derives_from_direction(self) -> None:
        entry = _make_entry(
            amount=Money(100, USD), direction=EntryDirection.DEBIT, occurred_at=datetime.now(UTC)
        )
        assert entry.signed_amount == Money(-100, USD)

    def test_credit_signed_amount_is_positive(self) -> None:
        entry = _make_entry(
            amount=Money(100, USD), direction=EntryDirection.CREDIT, occurred_at=datetime.now(UTC)
        )
        assert entry.signed_amount == Money(100, USD)


class TestOccurredAtMustBeTimezoneAware:
    def test_a_naive_timestamp_is_rejected(self) -> None:
        """design §3 — not one of the spec's own Entry scenarios verbatim, but the guard design
        introduces at this layer."""
        with pytest.raises(NaiveTimestampError):
            _make_entry(
                amount=Money(100, USD),
                direction=EntryDirection.DEBIT,
                occurred_at=datetime(2026, 1, 1),  # noqa: DTZ001 - the naive value under test
            )


class TestEntryIsImmutable:
    def test_no_mutator_exists(self) -> None:
        entry = _make_entry(
            amount=Money(100, USD), direction=EntryDirection.DEBIT, occurred_at=datetime.now(UTC)
        )
        with pytest.raises(AttributeError):
            entry.amount = Money(50, USD)  # type: ignore[misc]
