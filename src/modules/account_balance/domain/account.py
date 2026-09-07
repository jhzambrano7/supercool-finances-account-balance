from dataclasses import dataclass
from enum import Enum, auto
from typing import Self

from modules.account_balance.domain.errors import InvalidAccountPurposeError
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

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Account):
            return NotImplemented
        return self.account_id == other.account_id

    def __hash__(self) -> int:
        return hash(self.account_id)
