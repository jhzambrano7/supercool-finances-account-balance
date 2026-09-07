from abc import ABC, abstractmethod
from typing import Any

from modules.account_balance.application.gateways.models.find_accounts_criteria import (
    FindAccountCriteria,
)
from modules.account_balance.domain.account import Account
from modules.shared.application.errors import IntegrationError, ResourceNotFoundError


class AccountNaturalKeyConflictError(Exception):
    """Raised by an adapter when `add()` loses the natural-key race (AO4).

    Deliberately not a `DomainError`: whether `(owner_id, purpose, currency)`
    is already taken is a fact about *other rows*, which is the kind of
    question the account-balance domain spec explicitly says cannot be
    answered by any single `Account` instance (see its "Constraints Enforced
    Outside the Domain" table). This is a persistence-adapter concern, raised
    by whichever `AccountRepository` implementation backs a real database.
    """


class AccountNotFoundError(ResourceNotFoundError):
    """No account matches the given criteria.

    Raised by `get()` (find-or-fail), never by `find()` (find-or-`None`) --
    the two exist side by side precisely so a caller picks the one whose
    failure mode it actually wants, instead of every caller re-deriving
    "not found" from a `None` check.
    """

    def __init__(self, criteria: FindAccountCriteria) -> None:
        super().__init__(resource_type="account", resource_identifier=str(criteria))


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
        """Returns the account for this criteria, or `None` if none exists."""

    @abstractmethod
    async def add(self, account: Account) -> None:
        """Persists a newly-opened account.

        Raises `AccountNaturalKeyConflictError` if `(owner_id, purpose,
        currency)` already exists -- the losing side of the AO4 race.
        """
