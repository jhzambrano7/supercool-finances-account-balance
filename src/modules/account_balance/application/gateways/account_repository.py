from abc import ABC, abstractmethod
from typing import Any

from modules.account_balance.application.gateways.models.find_accounts_criteria import (
    FindAccountCriteria,
)
from modules.account_balance.domain.account import Account, AccountPurpose, UserAccount
from modules.account_balance.domain.identifiers import AccountId, OwnerId
from modules.shared.application.errors import (
    IntegrationError,
    ResourceAlreadyExistsError,
    ResourceNotFoundError,
)
from modules.shared.domain.money import Currency


class AccountNotFoundError(ResourceNotFoundError):
    """No account matches the given criteria.

    Raised by `get()` (find-or-fail), never by `find()` (find-or-`None`) --
    the two exist side by side precisely so a caller picks the one whose
    failure mode it actually wants, instead of every caller re-deriving
    "not found" from a `None` check.
    """

    def __init__(self, criteria: FindAccountCriteria) -> None:
        self.criteria = criteria
        super().__init__(resource_type="account", resource_identifier=str(criteria))


class AccountAlreadyExistsError(ResourceAlreadyExistsError):
    """Raised by an adapter when `add()` loses the natural-key race (AO4).

    Deliberately not a `DomainError`: whether `(owner_id, purpose, currency)`
    is already taken is a fact about *other rows*, which is the kind of
    question the account-balance domain spec explicitly says cannot be
    answered by any single `Account` instance (see its "Constraints Enforced
    Outside the Domain" table). This is a persistence-adapter concern, raised
    by whichever `AccountRepository` implementation backs a real database.

    `owner_id`/`purpose`/`currency` are kept as their own attributes, not
    only folded into `resource_identifier`'s string -- the same reasoning
    `ResourceAlreadyExistsError` itself is built on.
    """

    def __init__(self, *, owner_id: OwnerId, purpose: AccountPurpose, currency: Currency) -> None:
        self.owner_id = owner_id
        self.purpose = purpose
        self.currency = currency
        super().__init__(
            resource_type="account",
            resource_identifier=f"(owner={owner_id}, purpose={purpose.value}, currency={currency})",
        )


class AccountRepositoryError(IntegrationError):
    """An unrecognized failure crossed this port's boundary (point 4 of the
    adapter conventions: no third-party exception leaks past a repository).
    """

    def __init__(self, operation: str, cause: Exception, metadata: dict[str, Any]) -> None:
        super().__init__(
            code=f"ACCOUNT_REPOSITORY_ERROR.{operation}",
            cause=cause,
            message=f"An error occurred while performing the {operation} operation",
            metadata=metadata,
        )


class AccountRepository(ABC):
    """Port for account persistence (AO6) -- no SQL leaks through this signature.

    Collection-like, not a grab-bag of `find_by_x` methods: `find`/`get` take
    a `FindAccountCriteria` instead of each earning their own dedicated
    method, so a new way to look an account up is a new `Criteria`, not a new
    port method every adapter must implement.
    """

    async def get(self, *, criteria: FindAccountCriteria) -> Account:
        """Returns the account for this criteria, or raises `AccountNotFoundError`."""
        account = await self.find(criteria=criteria)
        if account is None:
            raise AccountNotFoundError(criteria)
        return account

    @abstractmethod
    async def find(self, *, criteria: FindAccountCriteria) -> Account | None:
        """Returns the account for this criteria, or `None` if none exists.

        For a `SYSTEM` account, the balance returned is computed as
        `SUM(signed entries)` at read time, never the stored
        `balance_amount` column (T7, PRD §5.3) -- that column goes unused
        for `SYSTEM` rows from this slice on. For a `USER` account, the
        stored column is the answer, unlocked.
        """

    @abstractmethod
    async def add(self, account: Account) -> None:
        """Persists a newly-opened account.

        Raises `AccountAlreadyExistsError` if `(owner_id, purpose, currency)`
        already exists -- the losing side of the AO4 race.
        """

    @abstractmethod
    async def get_for_update(self, account_id: AccountId) -> UserAccount | None:
        """Returns the `USER` account for this id, locked (`SELECT ... FOR
        UPDATE`) for the lifetime of the caller's transaction (T6, PRD §5
        step 3).

        `SYSTEM` accounts are never locked (T6, T7) -- the return type
        itself now says a `SYSTEM` account is not a possible result, rather
        than only a docstring promising callers won't ask for one.

        For a single lockable leg -- a deposit or a withdrawal, where exactly
        one side is a `USER` account -- this is the whole job and ordering
        does not arise. Locking two or more ids goes through
        `get_many_for_update`, which owns the ordering itself.

        No production caller yet: `TransferMoney._lock_user_accounts` calls
        `get_many_for_update` unconditionally today, single id included,
        since one adapter call handles both shapes. Kept deliberately --
        the explicit `deposit`/`withdraw` operations (`openspec/specs/
        transfer/spec.md`'s "Known gap") are the intended callers, each
        locking exactly one id and with no second leg to ever reason about
        ordering against.
        """

    @abstractmethod
    async def get_many_for_update(
        self, account_ids: tuple[AccountId, ...]
    ) -> tuple[UserAccount, ...]:
        """Locks several `USER` accounts at once (`SELECT ... FOR UPDATE`), for the lifetime of
        the caller's transaction.

        **Sorts the ids itself.** T6's deadlock avoidance depends on every
        transaction taking row locks in the same order, and that is this
        method's responsibility, not the caller's: a caller that has to
        remember to sort is a caller that will eventually forget, and the
        resulting deadlock would surface far from the omission. Pass the ids
        in whatever order you have them.

        Returns only the `USER` accounts that exist, so the result may be
        shorter than the input; it is never longer, and never contains a
        `SYSTEM` account (T6, T7).
        """

    @abstractmethod
    async def update(self, account: UserAccount) -> None:
        """Persists a `USER` account's new balance and version under the
        lock `get_for_update` already holds (T9).

        Only ever callable with a `UserAccount` (T7) -- a `SYSTEM` account's
        `balance_amount` column is never written by this path, and passing
        one here is now a type error, not a documented rule a caller could
        still violate at runtime. Does not commit: the enclosing
        `TransferUnitOfWork` commits once, atomically, alongside the posted
        entries and the idempotency record (T3).
        """
