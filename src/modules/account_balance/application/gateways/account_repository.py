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
        """Returns the account for this id, or `None` if none exists.

        For a `SYSTEM` account, the balance returned is computed as
        `SUM(signed entries)` at read time, never the stored
        `balance_amount` column (T7, PRD §5.3) -- that column goes unused
        for `SYSTEM` rows from this slice on. For a `USER` account, the
        stored column is the answer, unlocked.
        """

    @abstractmethod
    async def get_for_update(self, account_id: AccountId) -> Account | None:
        """Returns the `USER` account for this id, locked (`SELECT ... FOR
        UPDATE`) for the lifetime of the caller's transaction (T6, PRD §5
        step 3).

        `SYSTEM` accounts are never locked (T6, T7) -- callers must not
        invoke this for an id known to be a `SYSTEM` account; behaviour for
        one is unspecified (implementations return `None`, matching "not
        found" for this method's purpose, rather than silently locking
        infrastructure nothing needs locked). Callers determine `USER`-ness
        from an unlocked `get()` first, then lock only those ids, sorted by
        `AccountId`, before calling this.
        """

    @abstractmethod
    async def update(self, account: Account) -> None:
        """Persists a `USER` account's new balance and version under the
        lock `get_for_update` already holds (T9).

        Must only be called for a `USER` account (T7) -- a `SYSTEM`
        account's `balance_amount` column is never written by this path.
        Does not commit: the enclosing `TransferUnitOfWork` commits once,
        atomically, alongside the posted entries and the idempotency
        record (T3).
        """
