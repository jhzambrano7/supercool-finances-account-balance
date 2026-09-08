"""Property-based tests for `Account` (design §9.1 P3, P4).

Distinct from the example-based tests in `test_account.py`: these assert an
invariant holds over *any* generated sequence of operations, not one scripted
case. Scoped to direct `Account.debit`/`credit` sequences rather than the full
posting service, which does not exist yet (Work Unit 6) — see tasks.md T4.4.
"""

from datetime import UTC, datetime
from uuid import uuid4

from hypothesis import given
from hypothesis import strategies as st

from modules.account_balance.domain.account import (
    AccountPurpose,
    SystemAccount,
    UserAccount,
)
from modules.account_balance.domain.entry import Entry, EntryDirection
from modules.account_balance.domain.errors import InsufficientFundsError
from modules.account_balance.domain.identifiers import AccountId, EntryId, OwnerId, TransferId
from modules.shared.domain.money import Currency, Money

USD = Currency("USD")
_POOL_SIZE = 4


def _open_user_account() -> UserAccount:
    return UserAccount.open(
        account_id=AccountId(uuid4()),
        owner_id=OwnerId(uuid4()),
        purpose=AccountPurpose.CHECKING,
        currency=USD,
    )


def _entry(*, account_id: AccountId, direction: EntryDirection, amount: Money) -> Entry:
    return Entry(
        entry_id=EntryId(uuid4()),
        transfer_id=TransferId(uuid4()),
        account_id=account_id,
        direction=direction,
        amount=amount,
        occurred_at=datetime.now(UTC),
    )


_moves = st.lists(
    st.tuples(
        st.integers(min_value=0, max_value=_POOL_SIZE - 1),
        st.integers(min_value=0, max_value=_POOL_SIZE - 1),
        st.integers(min_value=1, max_value=1_000),
    ).filter(lambda move: move[0] != move[1]),
    max_size=30,
)


class TestPropertyP3TransferSequencesNeverGoNegativeAndConserve:
    """P3: over any sequence of debit/credit moves across a pool of USER accounts, every balance
    stays >= 0 and the sum across the pool is conserved (no move creates or destroys money — it
    only relocates it)."""

    @given(_moves)
    def test_user_balances_never_go_negative_and_pool_sum_is_conserved(
        self, moves: list[tuple[int, int, int]]
    ) -> None:
        pool = [_open_user_account() for _ in range(_POOL_SIZE)]

        for source_index, dest_index, amount in moves:
            source = pool[source_index]
            dest = pool[dest_index]
            debit_entry = _entry(
                account_id=source.account_id,
                direction=EntryDirection.DEBIT,
                amount=Money(amount, USD),
            )
            try:
                debited = source.debit(debit_entry)
            except InsufficientFundsError:
                continue  # refused move: neither account changes (I2)

            credited = dest.credit(
                _entry(
                    account_id=dest.account_id,
                    direction=EntryDirection.CREDIT,
                    amount=Money(amount, USD),
                )
            )
            pool[source_index] = debited
            pool[dest_index] = credited

        assert all(account.balance.amount >= 0 for account in pool)
        assert sum((account.balance.amount for account in pool), start=0) == 0


class TestPropertyP4BalanceEqualsSumOfSignedAmounts:
    """P4: after any sequence of entries applied to one `Account`, its balance equals the sum of
    `signed_amount` over every entry actually applied to it. Uses a `SYSTEM` account so no move
    is ever refused by the overdraft policy — the property is about the accounting identity, not
    I2."""

    @given(
        st.lists(
            st.tuples(
                st.sampled_from([EntryDirection.DEBIT, EntryDirection.CREDIT]),
                st.integers(min_value=1, max_value=1_000),
            ),
            max_size=30,
        )
    )
    def test_balance_equals_sum_of_applied_signed_amounts(
        self, moves: list[tuple[EntryDirection, int]]
    ) -> None:
        account: SystemAccount | UserAccount = SystemAccount(
            account_id=AccountId(uuid4()),
            owner_id=OwnerId(uuid4()),
            purpose=AccountPurpose.FUNDING,
            currency=USD,
            balance=Money.zero(USD),
        )

        expected = Money.zero(USD)
        for direction, amount in moves:
            entry = _entry(
                account_id=account.account_id, direction=direction, amount=Money(amount, USD)
            )
            account = (
                account.debit(entry) if direction is EntryDirection.DEBIT else account.credit(entry)
            )
            expected += entry.signed_amount

        assert account.balance == expected
