from abc import ABC, abstractmethod

from modules.account_balance.domain.account import Account, AccountPurpose
from modules.account_balance.domain.identifiers import AccountId, OwnerId
from modules.shared.domain.money import Currency


class AccountNaturalKeyConflictError(Exception):
    """Raised by an adapter when `add()` loses the natural-key race (AO4).

    Deliberately not a `DomainError`: whether `(owner_id, purpose, currency)`
    is already taken is a fact about *other rows*, which is the kind of
    question the account-balance domain spec explicitly says cannot be
    answered by any single `Account` instance (see its "Constraints Enforced
    Outside the Domain" table). This is a persistence-adapter concern, raised
    by whichever `AccountRepository` implementation backs a real database.
    """


class AccountRepository(ABC):
    """Port for account persistence (AO6) -- no SQL leaks through this signature."""

    @abstractmethod
    async def find_by_natural_key(
        self, *, owner_id: OwnerId, purpose: AccountPurpose, currency: Currency
    ) -> Account | None:
        """Returns the account for this natural key, or `None` if none exists yet."""

    @abstractmethod
    async def add(self, account: Account) -> None:
        """Persists a newly-opened account.

        Raises `AccountNaturalKeyConflictError` if `(owner_id, purpose,
        currency)` already exists -- the losing side of the AO4 race.
        """

    @abstractmethod
    async def get(self, account_id: AccountId) -> Account | None:
        """Returns the account for this id, or `None` if none exists."""
