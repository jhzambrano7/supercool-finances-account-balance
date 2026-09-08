from abc import ABC, abstractmethod
from types import TracebackType
from typing import Self

from modules.account_balance.application.gateways.account_repository import AccountRepository


class AccountUnitOfWork(ABC):
    """Boundary of the single transaction a lifecycle operation on one account must run inside
    (§7.2's closure, so far the only caller).

    Mirrors `TransferUnitOfWork`'s reasoning exactly, scoped down to the one repository a
    lifecycle operation needs: the `SELECT ... FOR UPDATE` lock `accounts.get_for_update` takes
    must be held from acquisition until this unit either commits (clean exit) or rolls back (an
    exception propagated) -- otherwise a concurrent write could land in the gap between reading
    the account and deciding it may close. Deliberately not `TransferUnitOfWork` itself: closing
    an account touches no transfer and no idempotency record, and bundling those repositories in
    here would misrepresent what this operation actually does.

    A caller obtains a *fresh* instance each time it needs one, same as `TransferUnitOfWork` --
    this type is not itself reused across `async with` blocks.
    """

    accounts: AccountRepository

    @abstractmethod
    async def __aenter__(self) -> Self:
        """Begins the transaction and binds `accounts` to it."""

    @abstractmethod
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Commits on a clean exit; rolls back if an exception propagated through the block."""
