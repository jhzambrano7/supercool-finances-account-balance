from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Self

from modules.account_balance.domain.entry import Entry, EntryDirection
from modules.account_balance.domain.errors import (
    AccountNotClosableError,
    AccountNotEmptyError,
    AccountNotOperableError,
    AccountOwnershipError,
    EntryAccountMismatchError,
    EntryDirectionMismatchError,
    InsufficientFundsError,
    InvalidAccountPurposeError,
)
from modules.account_balance.domain.identifiers import AccountId, OwnerId
from modules.shared.domain.errors import CurrencyMismatchError
from modules.shared.domain.money import Currency, Money


class AccountType(Enum):
    """`USER` or `SYSTEM` (PRD §4.1).

    String values, not `auto()`: this is persisted, so a reordering of the
    members must not silently reinterpret a stored row.
    """

    USER = "USER"
    SYSTEM = "SYSTEM"

    def is_user(self) -> bool:
        return self is AccountType.USER

    def is_system(self) -> bool:
        return self is AccountType.SYSTEM


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

    def matches_type(self, account_type: AccountType) -> bool:
        """Tell, don't ask (docs/coding-conventions.md): the caller states what it wants verified,
        `AccountPurpose` answers, rather than the caller reading `.account_type` back out and
        comparing it itself."""
        return self.account_type is account_type


class AccountStatus(Enum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"

    def is_active(self) -> bool:
        return self is AccountStatus.ACTIVE

    def is_closed(self) -> bool:
        return self is AccountStatus.CLOSED


@dataclass(frozen=True, slots=True, eq=False)
class UserAccount:
    """A `USER` account: real, persisted `balance`/`version` (§4, §4.2). Every behaviour that
    changes state returns a new instance — nothing here mutates `self` (design §9.3).

    `eq=False` because the generated dataclass equality would be by value, and two loads of the
    same account with different balances are the *same* account.
    """

    account_id: AccountId
    owner_id: OwnerId
    purpose: AccountPurpose
    currency: Currency
    balance: Money
    status: AccountStatus
    version: int

    def __post_init__(self) -> None:
        if not self.purpose.matches_type(AccountType.USER):
            raise InvalidAccountPurposeError(
                f"{self.purpose.value} does not belong to {AccountType.USER.value} (PRD §4.4)"
            )
        if self.balance.currency != self.currency:
            raise CurrencyMismatchError(
                f"account currency {self.currency} does not match balance currency "
                f"{self.balance.currency}"
            )
        if self.version < 0:
            raise ValueError(f"version must be >= 0, got {self.version}")

    @property
    def account_type(self) -> AccountType:
        return AccountType.USER

    @classmethod
    def open(
        cls,
        *,
        account_id: AccountId,
        owner_id: OwnerId,
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
        purpose: AccountPurpose,
        currency: Currency,
        balance: Money,
        status: AccountStatus,
        version: int,
    ) -> Self:
        """Restores an account exactly as stored — never re-derives its state.

        Deliberately does not re-assert a non-negative balance: a reversal
        legitimately leaves one negative (PRD §7.3), and refusing to load it
        would make the debt unrecoverable (design §4.1).
        """
        return cls(
            account_id=account_id,
            owner_id=owner_id,
            purpose=purpose,
            currency=currency,
            balance=balance,
            status=status,
            version=version,
        )

    def credit(self, entry: Entry) -> UserAccount:
        """Increases the balance — no ceiling."""
        resulting = self._validated_balance(entry, required_direction=EntryDirection.CREDIT)
        return replace(self, balance=resulting, version=self.version + 1)

    def debit(self, entry: Entry) -> UserAccount:
        """The ordinary debit path — refuses to drive the balance below zero (I2)."""
        resulting = self._validated_balance(entry, required_direction=EntryDirection.DEBIT)
        if resulting.is_negative:
            raise InsufficientFundsError(f"account {self.account_id} balance would go negative")
        return replace(self, balance=resulting, version=self.version + 1)

    def debit_for_reversal(self, entry: Entry) -> UserAccount:
        """The sole path that may drive the balance below zero (I2, §7.3).

        Does not check for a negative result at all — that is the entire
        difference from `debit()`. Referenced only here and in
        `posting.py::revert` (design §4.3's architecture test).
        """
        return replace(
            self,
            balance=self._validated_balance(entry, required_direction=EntryDirection.DEBIT),
            version=self.version + 1,
        )

    def assert_owned_by(self, owner_id: OwnerId) -> None:
        """States the ownership fact only — which legs to check is use-case policy (G5, §5.3)."""
        if owner_id != self.owner_id:
            raise AccountOwnershipError(f"account {self.account_id} is not owned by {owner_id}")

    def close(self) -> UserAccount:
        """Refuses unless ACTIVE and exactly zero balance (§7.2).

        Checked in the order the spec states the requirement: closability
        first (`AccountNotClosableError`), then balance (`AccountNotEmptyError`
        — the one refusal a caller can act on by emptying the account first).
        """
        if not self.is_closable():
            raise AccountNotClosableError(
                f"account {self.account_id} cannot be closed (status={self.status.value})"
            )
        if not self.balance.is_zero:
            raise AccountNotEmptyError(
                f"account {self.account_id} has a non-zero balance and cannot be closed"
            )
        return replace(self, status=AccountStatus.CLOSED, version=self.version + 1)

    def is_active(self) -> bool:
        return self.status.is_active()

    def is_closable(self) -> bool:
        """`ACTIVE` — a `SYSTEM` account is a different type and was never closable to begin
        with; there is no longer a second condition to check here (§7.2)."""
        return self.is_active()

    def fail_if_not_active(self) -> None:
        """An account whose status is not `ACTIVE` refuses debits and credits (§7.2)."""
        if not self.is_active():
            raise AccountNotOperableError(
                f"account {self.account_id} is not operable (status={self.status.value})"
            )

    def _validated_balance(self, entry: Entry, required_direction: EntryDirection) -> Money:
        """The shared preflight for every balance-moving method.

        Order matters: operability, then the entry actually belongs to this
        account, then the entry's direction matches the method being called.
        Currency agreement (I4) falls out of `Money.__add__` itself — there is
        no second currency check.
        """
        self.fail_if_not_active()
        if entry.account_id != self.account_id:
            raise EntryAccountMismatchError(
                f"entry {entry.entry_id} targets account {entry.account_id}, not {self.account_id}"
            )
        if entry.direction is not required_direction:
            raise EntryDirectionMismatchError(
                f"entry {entry.entry_id} has direction {entry.direction.value}, "
                f"expected {required_direction.value}"
            )
        return self.balance + entry.signed_amount

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, UserAccount):
            return NotImplemented
        return self.account_id == other.account_id

    def __hash__(self) -> int:
        return hash(self.account_id)


@dataclass(frozen=True, slots=True, eq=False)
class SystemAccount:
    """A `SYSTEM` account (`FUNDING`/`SETTLEMENT`, PRD §4.1): always `ACTIVE`, never closed, never
    locked (T6) — there is no `status`/`version`/`close()`/`is_closable()` here, not because they
    are unused but because neither concept applies: nothing can ever close this account (no
    method exists to try), and its optimistic-lock `version` is never persisted (T7 — `update()`
    must never be called for a `SYSTEM` account, so nothing ever reads or increments a real one).

    `balance` here is whatever the caller constructed it with. `credit()`/`debit()` still apply
    (unlimited — no floor or ceiling, unlike `UserAccount.debit()`) because a posting must be able
    to compute *some* resulting balance for this leg while applying it in-process (design §5.1);
    the repository is the one place that computes the persisted value fresh as `SUM(entries)` on
    every read (T7) rather than trusting any single in-memory snapshot.
    """

    account_id: AccountId
    owner_id: OwnerId
    purpose: AccountPurpose
    currency: Currency
    balance: Money

    def __post_init__(self) -> None:
        if not self.purpose.matches_type(AccountType.SYSTEM):
            raise InvalidAccountPurposeError(
                f"{self.purpose.value} does not belong to {AccountType.SYSTEM.value} (PRD §4.4)"
            )
        if self.balance.currency != self.currency:
            raise CurrencyMismatchError(
                f"account currency {self.currency} does not match balance currency "
                f"{self.balance.currency}"
            )

    @property
    def account_type(self) -> AccountType:
        return AccountType.SYSTEM

    def credit(self, entry: Entry) -> SystemAccount:
        """Increases the balance — no floor or ceiling."""
        resulting = self._validated_balance(entry, required_direction=EntryDirection.CREDIT)
        return replace(self, balance=resulting)

    def debit(self, entry: Entry) -> SystemAccount:
        """No floor — a `SYSTEM` account has unlimited overdraft by design (PRD §4.1)."""
        resulting = self._validated_balance(entry, required_direction=EntryDirection.DEBIT)
        return replace(self, balance=resulting)

    def debit_for_reversal(self, entry: Entry) -> SystemAccount:
        """Identical to `debit()` — there is no floor to route around either way. Kept as its own
        named method (not an alias) because `posting.py::revert` calls it by name polymorphically
        on whatever `source` is, and the architecture fitness test
        `test_debit_for_reversal_is_referenced_only_in_definition_and_revert` audits `def:` sites
        per class, not per alias target."""
        return self.debit(entry)

    def _validated_balance(self, entry: Entry, required_direction: EntryDirection) -> Money:
        """Same preflight as `UserAccount`, minus the operability check — a `SYSTEM` account has
        no status, it is never anything other than operable."""
        if entry.account_id != self.account_id:
            raise EntryAccountMismatchError(
                f"entry {entry.entry_id} targets account {entry.account_id}, not {self.account_id}"
            )
        if entry.direction is not required_direction:
            raise EntryDirectionMismatchError(
                f"entry {entry.entry_id} has direction {entry.direction.value}, "
                f"expected {required_direction.value}"
            )
        return self.balance + entry.signed_amount

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SystemAccount):
            return NotImplemented
        return self.account_id == other.account_id

    def __hash__(self) -> int:
        return hash(self.account_id)


Account = UserAccount | SystemAccount
