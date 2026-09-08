from datetime import UTC, datetime
from uuid import uuid4

import pytest

from modules.account_balance.domain.account import (
    AccountPurpose,
    AccountStatus,
    AccountType,
    SystemAccount,
    UserAccount,
)
from modules.account_balance.domain.entry import Entry, EntryDirection
from modules.account_balance.domain.errors import (
    AccountNotEmptyError,
    AccountNotOperableError,
    AccountOwnershipError,
    EntryAccountMismatchError,
    EntryDirectionMismatchError,
    InsufficientFundsError,
    InvalidAccountPurposeError,
)
from modules.account_balance.domain.identifiers import AccountId, EntryId, OwnerId, TransferId
from modules.shared.domain.errors import CurrencyMismatchError
from modules.shared.domain.money import Currency, Money

USD = Currency("USD")


def _account_id() -> AccountId:
    return AccountId(uuid4())


def _owner_id() -> OwnerId:
    return OwnerId(uuid4())


def _open_user_account(*, account_id: AccountId | None = None) -> UserAccount:
    return UserAccount.open(
        account_id=account_id or _account_id(),
        owner_id=_owner_id(),
        purpose=AccountPurpose.CHECKING,
        currency=USD,
    )


def _entry(
    *,
    account_id: AccountId,
    direction: EntryDirection,
    amount: Money,
) -> Entry:
    return Entry(
        entry_id=EntryId(uuid4()),
        transfer_id=TransferId(uuid4()),
        account_id=account_id,
        direction=direction,
        amount=amount,
        occurred_at=datetime.now(UTC),
    )


class TestOpen:
    def test_valid_pair_opens_at_zero_balance_and_active(self) -> None:
        account = UserAccount.open(
            account_id=_account_id(),
            owner_id=_owner_id(),
            purpose=AccountPurpose.CHECKING,
            currency=USD,
        )

        assert account.balance == Money.zero(USD)
        assert account.status is AccountStatus.ACTIVE

    def test_invalid_pair_is_rejected(self) -> None:
        """FUNDING cannot pair with a USER account — FUNDING belongs to SYSTEM (PRD §4.4)."""
        with pytest.raises(InvalidAccountPurposeError):
            UserAccount.open(
                account_id=_account_id(),
                owner_id=_owner_id(),
                purpose=AccountPurpose.FUNDING,
                currency=USD,
            )

    def test_opening_never_accepts_a_starting_balance(self) -> None:
        account = UserAccount.open(
            account_id=_account_id(),
            owner_id=_owner_id(),
            purpose=AccountPurpose.CHECKING,
            currency=USD,
        )

        assert account.balance == Money.zero(USD)
        assert account.version == 0


class TestReconstitute:
    def test_restores_a_non_zero_account_without_going_through_open(self) -> None:
        account = UserAccount.reconstitute(
            account_id=_account_id(),
            owner_id=_owner_id(),
            purpose=AccountPurpose.CHECKING,
            currency=USD,
            balance=Money(500, USD),
            status=AccountStatus.ACTIVE,
            version=7,
        )

        assert account.balance == Money(500, USD)
        assert account.version == 7

    def test_accepts_a_negative_user_balance_and_closed_status(self) -> None:
        """design §4.1: a reversal legitimately leaves a USER balance negative (PRD §7.3) —
        reconstitute must not re-assert non-negative, or the debt would be unrecoverable from
        storage."""
        account = UserAccount.reconstitute(
            account_id=_account_id(),
            owner_id=_owner_id(),
            purpose=AccountPurpose.CHECKING,
            currency=USD,
            balance=Money(-80, USD),
            status=AccountStatus.CLOSED,
            version=3,
        )

        assert account.balance == Money(-80, USD)
        assert account.status is AccountStatus.CLOSED


class TestDebit:
    def test_user_debit_refused_when_it_would_go_negative(self) -> None:
        account = _open_user_account()
        account = account.credit(
            _entry(
                account_id=account.account_id,
                direction=EntryDirection.CREDIT,
                amount=Money(50, USD),
            )
        )

        with pytest.raises(InsufficientFundsError):
            account.debit(
                _entry(
                    account_id=account.account_id,
                    direction=EntryDirection.DEBIT,
                    amount=Money(60, USD),
                )
            )
        assert account.balance == Money(50, USD)

    def test_user_debit_succeeds_down_to_exactly_zero(self) -> None:
        account = _open_user_account()
        account = account.credit(
            _entry(
                account_id=account.account_id,
                direction=EntryDirection.CREDIT,
                amount=Money(50, USD),
            )
        )

        debited = account.debit(
            _entry(
                account_id=account.account_id, direction=EntryDirection.DEBIT, amount=Money(50, USD)
            )
        )

        assert debited.balance == Money.zero(USD)

    def test_entry_for_a_different_account_raises_entry_account_mismatch(self) -> None:
        account = _open_user_account()
        with pytest.raises(EntryAccountMismatchError):
            account.debit(
                _entry(
                    account_id=_account_id(),
                    direction=EntryDirection.DEBIT,
                    amount=Money(1, USD),
                )
            )

    def test_credit_direction_entry_reaching_debit_raises_entry_direction_mismatch(self) -> None:
        account = _open_user_account()
        with pytest.raises(EntryDirectionMismatchError):
            account.debit(
                _entry(
                    account_id=account.account_id,
                    direction=EntryDirection.CREDIT,
                    amount=Money(1, USD),
                )
            )

    def test_returns_a_new_account_and_leaves_the_receiver_untouched(self) -> None:
        account = _open_user_account()
        account = account.credit(
            _entry(
                account_id=account.account_id,
                direction=EntryDirection.CREDIT,
                amount=Money(50, USD),
            )
        )

        debited = account.debit(
            _entry(
                account_id=account.account_id, direction=EntryDirection.DEBIT, amount=Money(20, USD)
            )
        )

        assert debited is not account
        assert debited.balance == Money(30, USD)
        assert account.balance == Money(50, USD)


class TestCredit:
    def test_credit_increases_the_balance(self) -> None:
        account = _open_user_account()

        credited = account.credit(
            _entry(
                account_id=account.account_id,
                direction=EntryDirection.CREDIT,
                amount=Money(75, USD),
            )
        )

        assert credited.balance == Money(75, USD)

    def test_entry_for_a_different_account_raises_entry_account_mismatch(self) -> None:
        account = _open_user_account()
        with pytest.raises(EntryAccountMismatchError):
            account.credit(
                _entry(
                    account_id=_account_id(),
                    direction=EntryDirection.CREDIT,
                    amount=Money(1, USD),
                )
            )

    def test_debit_direction_entry_reaching_credit_raises_entry_direction_mismatch(self) -> None:
        account = _open_user_account()
        with pytest.raises(EntryDirectionMismatchError):
            account.credit(
                _entry(
                    account_id=account.account_id,
                    direction=EntryDirection.DEBIT,
                    amount=Money(1, USD),
                )
            )

    def test_returns_a_new_account_and_leaves_the_receiver_untouched(self) -> None:
        account = _open_user_account()

        credited = account.credit(
            _entry(
                account_id=account.account_id,
                direction=EntryDirection.CREDIT,
                amount=Money(75, USD),
            )
        )

        assert credited is not account
        assert credited.balance == Money(75, USD)
        assert account.balance == Money.zero(USD)


class TestDebitForReversal:
    def test_succeeds_where_debit_would_refuse(self) -> None:
        account = _open_user_account()
        account = account.credit(
            _entry(
                account_id=account.account_id,
                direction=EntryDirection.CREDIT,
                amount=Money(20, USD),
            )
        )

        reversed_account = account.debit_for_reversal(
            _entry(
                account_id=account.account_id,
                direction=EntryDirection.DEBIT,
                amount=Money(100, USD),
            )
        )

        assert reversed_account.balance == Money(-80, USD)

    def test_the_ordinary_path_still_refuses_the_same_amount(self) -> None:
        account = _open_user_account()
        account = account.credit(
            _entry(
                account_id=account.account_id,
                direction=EntryDirection.CREDIT,
                amount=Money(20, USD),
            )
        )

        with pytest.raises(InsufficientFundsError):
            account.debit(
                _entry(
                    account_id=account.account_id,
                    direction=EntryDirection.DEBIT,
                    amount=Money(100, USD),
                )
            )

    def test_returns_a_new_account_and_leaves_the_receiver_untouched(self) -> None:
        account = _open_user_account()
        account = account.credit(
            _entry(
                account_id=account.account_id,
                direction=EntryDirection.CREDIT,
                amount=Money(20, USD),
            )
        )

        reversed_account = account.debit_for_reversal(
            _entry(
                account_id=account.account_id,
                direction=EntryDirection.DEBIT,
                amount=Money(100, USD),
            )
        )

        assert reversed_account is not account
        assert reversed_account.balance == Money(-80, USD)
        assert account.balance == Money(20, USD)


class TestCurrencyAgreement:
    """design §4.2: "there is no second currency check" -- I4 at the `Account` level falls out of
    `Money.__add__` itself, not a dedicated guard. That was implemented but untested on the
    public API (verify-report WARNING-1): nothing would have caught a regression that reordered
    `_validated_balance` to check something else first and skip past this. One test per public
    balance-moving method closes it."""

    def test_debit_with_a_mismatched_currency_entry_raises_currency_mismatch(self) -> None:
        account = _open_user_account()  # USD
        with pytest.raises(CurrencyMismatchError):
            account.debit(
                _entry(
                    account_id=account.account_id,
                    direction=EntryDirection.DEBIT,
                    amount=Money(1, Currency("EUR")),
                )
            )

    def test_credit_with_a_mismatched_currency_entry_raises_currency_mismatch(self) -> None:
        account = _open_user_account()  # USD
        with pytest.raises(CurrencyMismatchError):
            account.credit(
                _entry(
                    account_id=account.account_id,
                    direction=EntryDirection.CREDIT,
                    amount=Money(1, Currency("EUR")),
                )
            )

    def test_debit_for_reversal_with_a_mismatched_currency_entry_raises_currency_mismatch(
        self,
    ) -> None:
        account = _open_user_account()  # USD
        with pytest.raises(CurrencyMismatchError):
            account.debit_for_reversal(
                _entry(
                    account_id=account.account_id,
                    direction=EntryDirection.DEBIT,
                    amount=Money(1, Currency("EUR")),
                )
            )


class TestOperability:
    def test_a_closed_account_refuses_a_debit(self) -> None:
        account = _open_user_account()
        closed = UserAccount.reconstitute(
            account_id=account.account_id,
            owner_id=account.owner_id,
            purpose=account.purpose,
            currency=account.currency,
            balance=Money.zero(USD),
            status=AccountStatus.CLOSED,
            version=account.version,
        )

        with pytest.raises(AccountNotOperableError):
            closed.debit(
                _entry(
                    account_id=closed.account_id,
                    direction=EntryDirection.DEBIT,
                    amount=Money(1, USD),
                )
            )

    def test_a_closed_account_refuses_a_credit(self) -> None:
        account = _open_user_account()
        closed = UserAccount.reconstitute(
            account_id=account.account_id,
            owner_id=account.owner_id,
            purpose=account.purpose,
            currency=account.currency,
            balance=Money.zero(USD),
            status=AccountStatus.CLOSED,
            version=account.version,
        )

        with pytest.raises(AccountNotOperableError):
            closed.credit(
                _entry(
                    account_id=closed.account_id,
                    direction=EntryDirection.CREDIT,
                    amount=Money(1, USD),
                )
            )


class TestOwnership:
    def test_owner_mismatch_is_rejected(self) -> None:
        account = _open_user_account()
        with pytest.raises(AccountOwnershipError):
            account.assert_owned_by(_owner_id())

    def test_owner_match_passes_silently(self) -> None:
        account = _open_user_account()
        account.assert_owned_by(account.owner_id)  # no error


class TestClose:
    def test_zero_balance_user_account_closes(self) -> None:
        account = _open_user_account()

        closed = account.close()

        assert closed.status is AccountStatus.CLOSED

    def test_non_zero_balance_blocks_closure(self) -> None:
        account = _open_user_account()
        account = account.credit(
            _entry(
                account_id=account.account_id,
                direction=EntryDirection.CREDIT,
                amount=Money(10, USD),
            )
        )

        with pytest.raises(AccountNotEmptyError):
            account.close()
        assert account.status is AccountStatus.ACTIVE

    def test_system_accounts_have_no_balance_bearing_operations(self) -> None:
        """The ADT split states as types what used to be runtime refusals or dead computation.

        `close()`/`is_closable()`: previously an `AccountNotClosableError` raised at runtime for a
        `SYSTEM` account (design §4.5) -- now there is nothing to call. `balance`, `credit()`,
        `debit()`, `debit_for_reversal()`, `status`, `version`: a `SYSTEM` account has no balance
        anywhere (T7), so it has no state for any of them to advance. Both facts are checked here
        rather than through a raise, because the point is that the calls no longer type-check.
        """
        for absent in (
            "close",
            "is_closable",
            "balance",
            "credit",
            "debit",
            "debit_for_reversal",
            "status",
            "version",
        ):
            assert not hasattr(SystemAccount, absent), f"SystemAccount should not define {absent}"

    def test_returns_a_new_account_and_leaves_the_receiver_untouched(self) -> None:
        account = _open_user_account()

        closed = account.close()

        assert closed is not account
        assert closed.status is AccountStatus.CLOSED
        assert account.status is AccountStatus.ACTIVE


class TestTellDontAsk:
    """The enums and Account answer questions about their own state (docs/ coding-conventions.md)
    instead of exposing raw values for callers to branch on themselves."""

    def test_account_status_knows_whether_it_is_active(self) -> None:
        assert AccountStatus.ACTIVE.is_active()
        assert not AccountStatus.CLOSED.is_active()

    def test_account_status_knows_whether_it_is_closed(self) -> None:
        assert AccountStatus.CLOSED.is_closed()
        assert not AccountStatus.ACTIVE.is_closed()

    def test_account_type_knows_whether_it_is_user_or_system(self) -> None:
        assert AccountType.USER.is_user()
        assert not AccountType.USER.is_system()
        assert AccountType.SYSTEM.is_system()
        assert not AccountType.SYSTEM.is_user()

    def test_account_purpose_knows_whether_it_matches_a_type(self) -> None:
        assert AccountPurpose.CHECKING.matches_type(AccountType.USER)
        assert not AccountPurpose.CHECKING.matches_type(AccountType.SYSTEM)
        assert AccountPurpose.FUNDING.matches_type(AccountType.SYSTEM)
        assert not AccountPurpose.FUNDING.matches_type(AccountType.USER)

    def test_account_knows_whether_it_is_active(self) -> None:
        account = _open_user_account()

        assert account.is_active()
        assert not account.close().is_active()

    def test_account_knows_whether_it_is_closable(self) -> None:
        assert _open_user_account().is_closable()
        assert not _open_user_account().close().is_closable()
