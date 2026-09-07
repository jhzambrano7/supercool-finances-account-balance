from uuid import uuid4

import pytest

from modules.account_balance.domain.account import (
    Account,
    AccountPurpose,
    AccountStatus,
    AccountType,
)
from modules.account_balance.domain.errors import InvalidAccountPurposeError
from modules.account_balance.domain.identifiers import AccountId, OwnerId
from modules.shared.domain.money import Currency, Money

USD = Currency("USD")


def _account_id() -> AccountId:
    return AccountId(uuid4())


def _owner_id() -> OwnerId:
    return OwnerId(uuid4())


class TestOpen:
    def test_valid_pair_opens_at_zero_balance_and_active(self) -> None:
        account = Account.open(
            account_id=_account_id(),
            owner_id=_owner_id(),
            account_type=AccountType.USER,
            purpose=AccountPurpose.CHECKING,
            currency=USD,
        )

        assert account.balance == Money.zero(USD)
        assert account.status is AccountStatus.ACTIVE

    def test_invalid_pair_is_rejected(self) -> None:
        """USER cannot pair with FUNDING — FUNDING belongs to SYSTEM (PRD §4.4)."""
        with pytest.raises(InvalidAccountPurposeError):
            Account.open(
                account_id=_account_id(),
                owner_id=_owner_id(),
                account_type=AccountType.USER,
                purpose=AccountPurpose.FUNDING,
                currency=USD,
            )

    @pytest.mark.parametrize(
        ("account_type", "purpose"),
        [
            (AccountType.USER, AccountPurpose.CHECKING),
            (AccountType.SYSTEM, AccountPurpose.FUNDING),
        ],
    )
    def test_opening_never_accepts_a_starting_balance(
        self, account_type: AccountType, purpose: AccountPurpose
    ) -> None:
        account = Account.open(
            account_id=_account_id(),
            owner_id=_owner_id(),
            account_type=account_type,
            purpose=purpose,
            currency=USD,
        )

        assert account.balance == Money.zero(USD)
        assert account.version == 0


class TestReconstitute:
    def test_restores_a_non_zero_account_without_going_through_open(self) -> None:
        account = Account.reconstitute(
            account_id=_account_id(),
            owner_id=_owner_id(),
            account_type=AccountType.USER,
            purpose=AccountPurpose.CHECKING,
            currency=USD,
            balance=Money(500, USD),
            status=AccountStatus.ACTIVE,
            version=7,
        )

        assert account.balance == Money(500, USD)
        assert account.version == 7

    def test_accepts_a_negative_user_balance_and_closed_status(self) -> None:
        """design §4.1: a reversal legitimately leaves a USER balance negative

        (PRD §7.3) — reconstitute must not re-assert non-negative, or the
        debt would be unrecoverable from storage.
        """
        account = Account.reconstitute(
            account_id=_account_id(),
            owner_id=_owner_id(),
            account_type=AccountType.USER,
            purpose=AccountPurpose.CHECKING,
            currency=USD,
            balance=Money(-80, USD),
            status=AccountStatus.CLOSED,
            version=3,
        )

        assert account.balance == Money(-80, USD)
        assert account.status is AccountStatus.CLOSED
