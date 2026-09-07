from dataclasses import dataclass, replace
from enum import Enum, auto
from typing import Self

from modules.account_balance.domain.entry import Entry, EntryDirection
from modules.account_balance.domain.errors import (
    AccountNotOperableError,
    EntryAccountMismatchError,
    EntryDirectionMismatchError,
    InsufficientFundsError,
    InvalidAccountPurposeError,
)
from modules.account_balance.domain.identifiers import AccountId, OwnerId
from modules.shared.domain.errors import CurrencyMismatchError
from modules.shared.domain.money import Currency, Money


class OverdraftPolicy(Enum):
    """Whether an account's balance may cross zero, and under which method.

    Never persisted — derived from `AccountType` on every read — so it uses
    `auto()` rather than the string values the persisted enums below use.
    """

    FORBIDDEN = auto()
    UNLIMITED = auto()

    def assert_allows(self, resulting_balance: Money, *, account_id: AccountId) -> None:
        """Raises `InsufficientFundsError` (I2) when the policy forbids crossing zero.

        The message deliberately carries no amount (PRD §11.4) — only the
        account id, which is a structured field the caller may log alongside.
        """
        if self is OverdraftPolicy.FORBIDDEN and resulting_balance.is_negative:
            raise InsufficientFundsError(f"account {account_id} balance would go negative")


class AccountType(Enum):
    """`USER` or `SYSTEM` (PRD §4.1) — drives the overdraft policy.

    String values, not `auto()`: this is persisted, so a reordering of the
    members must not silently reinterpret a stored row.
    """

    USER = "USER"
    SYSTEM = "SYSTEM"

    @property
    def overdraft_policy(self) -> OverdraftPolicy:
        if self is AccountType.USER:
            return OverdraftPolicy.FORBIDDEN
        return OverdraftPolicy.UNLIMITED


class AccountPurpose(Enum):
    """What an account is *for* — a separate axis from `AccountType` (PRD §4.4)."""

    CHECKING = "CHECKING"
    SAVINGS = "SAVINGS"
    FUNDING = "FUNDING"
    SETTLEMENT = "SETTLEMENT"

    @property
    def account_type(self) -> AccountType:
        """Each purpose belongs to exactly one type (PRD §4.4) — a total function."""
        if self in (AccountPurpose.CHECKING, AccountPurpose.SAVINGS):
            return AccountType.USER
        return AccountType.SYSTEM


class AccountStatus(Enum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


@dataclass(frozen=True, slots=True, eq=False)
class Account:
    """An immutable entity: identity is `account_id`, never balance or status.

    `eq=False` because the generated dataclass equality would be by value,
    and two loads of the same account with different balances are the *same*
    account. Every behaviour that changes state returns a new instance —
    nothing here mutates `self` (design §4, §4.2).
    """

    account_id: AccountId
    owner_id: OwnerId
    account_type: AccountType
    purpose: AccountPurpose
    currency: Currency
    balance: Money
    status: AccountStatus
    version: int

    def __post_init__(self) -> None:
        if self.purpose.account_type is not self.account_type:
            raise InvalidAccountPurposeError(
                f"{self.purpose.value} does not belong to {self.account_type.value} (PRD §4.4)"
            )
        if self.balance.currency != self.currency:
            raise CurrencyMismatchError(
                f"account currency {self.currency} does not match balance currency "
                f"{self.balance.currency}"
            )
        if self.version < 0:
            raise ValueError(f"version must be >= 0, got {self.version}")

    @classmethod
    def open(
        cls,
        *,
        account_id: AccountId,
        owner_id: OwnerId,
        account_type: AccountType,
        purpose: AccountPurpose,
        currency: Currency,
    ) -> Self:
        """An account is never born holding money (G1) — balance is forced to zero.

        Always `ACTIVE`, always `version=0`. Loading a previously-persisted,
        non-zero account must use `reconstitute()` instead.
        """
        return cls(
            account_id=account_id,
            owner_id=owner_id,
            account_type=account_type,
            purpose=purpose,
            currency=currency,
            balance=Money.zero(currency),
            status=AccountStatus.ACTIVE,
            version=0,
        )

    @classmethod
    def reconstitute(
        cls,
        *,
        account_id: AccountId,
        owner_id: OwnerId,
        account_type: AccountType,
        purpose: AccountPurpose,
        currency: Currency,
        balance: Money,
        status: AccountStatus,
        version: int,
    ) -> Self:
        """Restores an account exactly as stored — never re-derives its state.

        Deliberately does not re-assert a non-negative `USER` balance: a
        reversal legitimately leaves one negative (PRD §7.3), and refusing to
        load it would make the debt unrecoverable (design §4.1).
        """
        return cls(
            account_id=account_id,
            owner_id=owner_id,
            account_type=account_type,
            purpose=purpose,
            currency=currency,
            balance=balance,
            status=status,
            version=version,
        )

    def assert_operable(self) -> None:
        """An account whose status is not `ACTIVE` refuses debits and credits (§7.2)."""
        if self.status is not AccountStatus.ACTIVE:
            raise AccountNotOperableError(
                f"account {self.account_id} is not operable (status={self.status.value})"
            )

    def _validated_balance(self, entry: Entry, direction: EntryDirection) -> Money:
        """The shared preflight for every balance-moving method.

        Order matters: operability, then the entry actually belongs to this
        account, then the entry's direction matches the method being called.
        Currency agreement (I4) falls out of `Money.__add__` itself — there is
        no second currency check.
        """
        self.assert_operable()
        if entry.account_id != self.account_id:
            raise EntryAccountMismatchError(
                f"entry {entry.entry_id} targets account {entry.account_id}, not {self.account_id}"
            )
        if entry.direction is not direction:
            raise EntryDirectionMismatchError(
                f"entry {entry.entry_id} has direction {entry.direction.value}, "
                f"expected {direction.value}"
            )
        return self.balance + entry.signed_amount

    def credit(self, entry: Entry) -> Account:
        """Increases the balance, for `USER` and `SYSTEM` accounts alike — no floor or ceiling."""
        resulting = self._validated_balance(entry, EntryDirection.CREDIT)
        return replace(self, balance=resulting, version=self.version + 1)

    def debit(self, entry: Entry) -> Account:
        """The ordinary debit path — refuses to drive a `USER` account below zero (I2)."""
        resulting = self._validated_balance(entry, EntryDirection.DEBIT)
        self.account_type.overdraft_policy.assert_allows(resulting, account_id=self.account_id)
        return replace(self, balance=resulting, version=self.version + 1)

    def debit_for_reversal(self, entry: Entry) -> Account:
        """The sole path that may drive a `USER` account below zero (I2, §7.3).

        Does not consult `OverdraftPolicy` at all — that is the entire
        difference from `debit()`. Referenced only here and in
        `posting.py::revert` (design §4.3's architecture test).
        """
        return replace(
            self,
            balance=self._validated_balance(entry, EntryDirection.DEBIT),
            version=self.version + 1,
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Account):
            return NotImplemented
        return self.account_id == other.account_id

    def __hash__(self) -> int:
        return hash(self.account_id)
