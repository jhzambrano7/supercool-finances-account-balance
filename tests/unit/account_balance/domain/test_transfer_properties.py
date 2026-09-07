"""Property-based test for I1's general per-currency form (design §9.1 P2).

`Transfer.__post_init__` must succeed iff every currency present among its
entries nets to zero — not merely "exactly one debit and one credit" — since
a future FX posting (or an unaffordable-reversal posting) adds legs without
reshaping the invariant. This exercises the general form, including synthetic
multi-currency (four-leg-and-up) sets, ahead of any caller that needs it
(Work Unit 6's posting service does not exist yet).
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st

from modules.account_balance.domain.entry import Entry, EntryDirection
from modules.account_balance.domain.errors import UnbalancedTransferError
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
EUR = Currency("EUR")
_CURRENCIES = (USD, EUR)


def _entry(
    *,
    transfer_id: TransferId,
    account_id: AccountId,
    direction: EntryDirection,
    currency: Currency,
    amount: int,
) -> Entry:
    return Entry(
        entry_id=EntryId(uuid4()),
        transfer_id=transfer_id,
        account_id=account_id,
        direction=direction,
        amount=Money(amount, currency),
        occurred_at=datetime.now(UTC),
    )


def _signed(direction: EntryDirection, amount: int) -> int:
    return amount if direction is EntryDirection.CREDIT else -amount


_extra_legs = st.lists(
    st.tuples(
        st.sampled_from(_CURRENCIES),
        st.sampled_from([EntryDirection.DEBIT, EntryDirection.CREDIT]),
        st.integers(min_value=1, max_value=1_000),
        st.sampled_from(["source", "destination"]),
    ),
    max_size=4,
)


class TestPropertyP2NetsToZeroPerCurrencyGeneralForm:
    """P2: construction succeeds iff every currency present nets to zero.

    A baseline balanced `USD` pair (source debit, destination credit)
    guarantees the leg-shape guard always passes, so only I1 varies with the
    hypothesis-generated extra legs across `USD`/`EUR`.
    """

    @given(base_amount=st.integers(min_value=1, max_value=1_000), extra_legs=_extra_legs)
    def test_construction_succeeds_iff_every_currency_nets_to_zero(
        self,
        base_amount: int,
        extra_legs: list[tuple[Currency, EntryDirection, int, str]],
    ) -> None:
        transfer_id = TransferId(uuid4())
        source = AccountId(uuid4())
        destination = AccountId(uuid4())
        when = datetime.now(UTC)

        entries: list[Entry] = []
        totals: dict[Currency, int] = {}

        def _add_leg(
            account_id: AccountId, direction: EntryDirection, currency: Currency, amount: int
        ) -> None:
            entries.append(
                _entry(
                    transfer_id=transfer_id,
                    account_id=account_id,
                    direction=direction,
                    currency=currency,
                    amount=amount,
                )
            )
            totals[currency] = totals.get(currency, 0) + _signed(direction, amount)

        _add_leg(source, EntryDirection.DEBIT, USD, base_amount)
        _add_leg(destination, EntryDirection.CREDIT, USD, base_amount)
        for currency, direction, amount, account_name in extra_legs:
            account_id = source if account_name == "source" else destination
            _add_leg(account_id, direction, currency, amount)

        balanced = all(total == 0 for total in totals.values())

        def _build() -> Transfer:
            return Transfer(
                transfer_id=transfer_id,
                source_account_id=source,
                destination_account_id=destination,
                amount=Money(base_amount, USD),
                requested_by=OwnerId(uuid4()),
                idempotency_key=IdempotencyKey("prop-key"),
                occurred_at=when,
                entries=tuple(entries),
            )

        if balanced:
            transfer = _build()
            assert transfer.entries == tuple(entries)
        else:
            with pytest.raises(UnbalancedTransferError):
                _build()
