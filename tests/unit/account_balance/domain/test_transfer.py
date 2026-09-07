"""Example-based tests for `Transfer` (design §5, spec "Transfer" section).

Cross-currency rejection ("Cross-currency transfer rejected") is deliberately
NOT tested here: `Transfer.__post_init__`'s own guard table (design §5) has no
currency-match check of its own — a cross-currency pair of legs is instead
caught by I1 netting (each currency's total is individually nonzero), which
would raise `UnbalancedTransferError`, not the spec's stated
`CurrencyMismatchError`. That scenario belongs to the posting service's own
guard (design §5.1 step 1, I4) and is deferred to Work Unit 6's tests
(tasks.md T5.1's own note), not forced here against a guard this type does
not have.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from modules.account_balance.domain.entry import Entry, EntryDirection
from modules.account_balance.domain.errors import (
    MalformedTransferError,
    NaiveTimestampError,
    NonPositiveAmountError,
    SelfTransferError,
    UnbalancedTransferError,
)
from modules.account_balance.domain.identifiers import (
    AccountId,
    EntryId,
    IdempotencyKey,
    OwnerId,
    TransferId,
)
from modules.account_balance.domain.transfer import Transfer
from modules.shared.domain.money import Currency, Money

USD = Currency("USD")


def _entry(
    *,
    transfer_id: TransferId,
    account_id: AccountId,
    direction: EntryDirection,
    amount: Money,
    occurred_at: datetime | None = None,
) -> Entry:
    return Entry(
        entry_id=EntryId(uuid4()),
        transfer_id=transfer_id,
        account_id=account_id,
        direction=direction,
        amount=amount,
        occurred_at=occurred_at or datetime.now(UTC),
    )


def _make_transfer(
    *,
    transfer_id: TransferId | None = None,
    source_account_id: AccountId | None = None,
    destination_account_id: AccountId | None = None,
    amount: Money | None = None,
    requested_by: OwnerId | None = None,
    idempotency_key: IdempotencyKey | None = None,
    occurred_at: datetime | None = None,
    entries: tuple[Entry, ...] | None = None,
) -> Transfer:
    tid = transfer_id or TransferId(uuid4())
    source = source_account_id or AccountId(uuid4())
    destination = destination_account_id or AccountId(uuid4())
    the_amount = amount if amount is not None else Money(100, USD)
    when = occurred_at or datetime.now(UTC)
    legs = (
        entries
        if entries is not None
        else (
            _entry(
                transfer_id=tid,
                account_id=source,
                direction=EntryDirection.DEBIT,
                amount=the_amount,
                occurred_at=when,
            ),
            _entry(
                transfer_id=tid,
                account_id=destination,
                direction=EntryDirection.CREDIT,
                amount=the_amount,
                occurred_at=when,
            ),
        )
    )
    return Transfer(
        transfer_id=tid,
        source_account_id=source,
        destination_account_id=destination,
        amount=the_amount,
        requested_by=requested_by or OwnerId(uuid4()),
        idempotency_key=idempotency_key or IdempotencyKey("idem-key-1"),
        occurred_at=when,
        entries=legs,
    )


class TestNetsToZeroPerCurrency:
    def test_two_balanced_legs_pass(self) -> None:
        transfer = _make_transfer()

        assert len(transfer.entries) == 2

    def test_an_unbalanced_pair_is_rejected(self) -> None:
        tid = TransferId(uuid4())
        source = AccountId(uuid4())
        destination = AccountId(uuid4())
        legs = (
            _entry(
                transfer_id=tid,
                account_id=source,
                direction=EntryDirection.DEBIT,
                amount=Money(100, USD),
            ),
            _entry(
                transfer_id=tid,
                account_id=destination,
                direction=EntryDirection.CREDIT,
                amount=Money(90, USD),
            ),
        )

        with pytest.raises(UnbalancedTransferError):
            _make_transfer(
                transfer_id=tid,
                source_account_id=source,
                destination_account_id=destination,
                entries=legs,
            )


class TestAmountIsStrictlyPositive:
    def test_zero_amount_rejected(self) -> None:
        with pytest.raises(NonPositiveAmountError):
            _make_transfer(amount=Money(0, USD))

    def test_negative_amount_rejected(self) -> None:
        with pytest.raises(NonPositiveAmountError):
            _make_transfer(amount=Money(-10, USD))


class TestSourceAndDestinationMustDiffer:
    def test_self_transfer_rejected(self) -> None:
        account_id = AccountId(uuid4())

        with pytest.raises(SelfTransferError):
            _make_transfer(source_account_id=account_id, destination_account_id=account_id)


class TestTransferIsFrozenAndCarriesNoStatus:
    def test_no_status_field_and_no_attribute_is_reassignable(self) -> None:
        transfer = _make_transfer()

        assert not hasattr(transfer, "status")
        with pytest.raises(AttributeError):
            transfer.amount = Money(50, USD)  # type: ignore[misc]


class TestProvenanceIsRetained:
    def test_idempotency_key_and_requested_by_are_retained(self) -> None:
        owner = OwnerId(uuid4())
        key = IdempotencyKey("a-client-generated-key")

        transfer = _make_transfer(requested_by=owner, idempotency_key=key)

        assert transfer.idempotency_key == key
        assert transfer.requested_by == owner


class TestTransfersLegsMustDescribeThatTransfer:
    def test_fewer_than_two_entries_raises(self) -> None:
        tid = TransferId(uuid4())
        source = AccountId(uuid4())
        destination = AccountId(uuid4())
        single_leg = (
            _entry(
                transfer_id=tid,
                account_id=source,
                direction=EntryDirection.DEBIT,
                amount=Money(100, USD),
            ),
        )

        with pytest.raises(MalformedTransferError):
            _make_transfer(
                transfer_id=tid,
                source_account_id=source,
                destination_account_id=destination,
                entries=single_leg,
            )

    def test_a_leg_belonging_to_another_transfer_is_rejected(self) -> None:
        tid = TransferId(uuid4())
        other_tid = TransferId(uuid4())
        source = AccountId(uuid4())
        destination = AccountId(uuid4())
        legs = (
            _entry(
                transfer_id=tid,
                account_id=source,
                direction=EntryDirection.DEBIT,
                amount=Money(100, USD),
            ),
            _entry(
                transfer_id=other_tid,
                account_id=destination,
                direction=EntryDirection.CREDIT,
                amount=Money(100, USD),
            ),
        )

        with pytest.raises(MalformedTransferError):
            _make_transfer(
                transfer_id=tid,
                source_account_id=source,
                destination_account_id=destination,
                entries=legs,
            )

    def test_legs_that_do_not_represent_source_and_destination_are_rejected(self) -> None:
        """Roles reversed: source is credited, destination is debited.

        Nets to zero (100 - 100 = 0), isolating the malformed shape check
        from I1 — this must fail before netting is ever computed.
        """
        tid = TransferId(uuid4())
        source = AccountId(uuid4())
        destination = AccountId(uuid4())
        legs = (
            _entry(
                transfer_id=tid,
                account_id=source,
                direction=EntryDirection.CREDIT,
                amount=Money(100, USD),
            ),
            _entry(
                transfer_id=tid,
                account_id=destination,
                direction=EntryDirection.DEBIT,
                amount=Money(100, USD),
            ),
        )

        with pytest.raises(MalformedTransferError):
            _make_transfer(
                transfer_id=tid,
                source_account_id=source,
                destination_account_id=destination,
                entries=legs,
            )


class TestOccurredAtMustBeTimezoneAware:
    def test_a_naive_timestamp_is_rejected(self) -> None:
        with pytest.raises(NaiveTimestampError):
            _make_transfer(occurred_at=datetime(2026, 1, 1))  # noqa: DTZ001 - naive value under test


class TestGuardOrder:
    """Design §5 states an explicit guard order; these confirm the exact

    precedence used when multiple problems are present at once, since the
    result depends on which guard runs first.
    """

    def test_zero_amount_takes_precedence_over_self_transfer(self) -> None:
        account_id = AccountId(uuid4())

        with pytest.raises(NonPositiveAmountError):
            _make_transfer(
                amount=Money(0, USD),
                source_account_id=account_id,
                destination_account_id=account_id,
            )

    def test_self_transfer_takes_precedence_over_malformed_shape(self) -> None:
        account_id = AccountId(uuid4())
        tid = TransferId(uuid4())
        single_leg = (
            _entry(
                transfer_id=tid,
                account_id=account_id,
                direction=EntryDirection.DEBIT,
                amount=Money(100, USD),
            ),
        )

        with pytest.raises(SelfTransferError):
            _make_transfer(
                transfer_id=tid,
                source_account_id=account_id,
                destination_account_id=account_id,
                entries=single_leg,
            )

    def test_malformed_shape_takes_precedence_over_naive_timestamp(self) -> None:
        tid = TransferId(uuid4())
        source = AccountId(uuid4())
        destination = AccountId(uuid4())
        single_leg = (
            _entry(
                transfer_id=tid,
                account_id=source,
                direction=EntryDirection.DEBIT,
                amount=Money(100, USD),
            ),
        )

        with pytest.raises(MalformedTransferError):
            _make_transfer(
                transfer_id=tid,
                source_account_id=source,
                destination_account_id=destination,
                entries=single_leg,
                occurred_at=datetime(2026, 1, 1),  # noqa: DTZ001 - naive value under test
            )

    def test_naive_timestamp_takes_precedence_over_unbalanced(self) -> None:
        tid = TransferId(uuid4())
        source = AccountId(uuid4())
        destination = AccountId(uuid4())
        unbalanced_legs = (
            _entry(
                transfer_id=tid,
                account_id=source,
                direction=EntryDirection.DEBIT,
                amount=Money(100, USD),
                occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
            _entry(
                transfer_id=tid,
                account_id=destination,
                direction=EntryDirection.CREDIT,
                amount=Money(90, USD),
                occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        )

        with pytest.raises(NaiveTimestampError):
            _make_transfer(
                transfer_id=tid,
                source_account_id=source,
                destination_account_id=destination,
                entries=unbalanced_legs,
                occurred_at=datetime(2026, 1, 1),  # noqa: DTZ001 - naive value under test
            )
