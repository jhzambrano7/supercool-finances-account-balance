from abc import ABC, abstractmethod
from types import TracebackType
from typing import Self

from modules.account_balance.application.gateways.account_repository import AccountRepository
from modules.account_balance.application.gateways.idempotency_repository import (
    IdempotencyRepository,
)
from modules.account_balance.application.gateways.transfer_repository import TransferRepository


class TransferUnitOfWork(ABC):
    """Boundary of the single transaction a transfer's posting must run
    inside (T3, PRD §5 -- "all inside a single database transaction").

    `accounts`, `transfers` and `idempotency` are bound to the *same*
    transaction for the lifetime of one `async with` block: the
    `SELECT ... FOR UPDATE` locks `accounts.get_for_update` takes are held
    from acquisition until this unit either commits (clean exit) or rolls
    back (an exception propagates out of the block) -- never released in
    between. The use case, not any individual repository call, decides when
    that happens (design §5.3's "persisting the Posting and the idempotency
    record in one transaction | Use case").

    A caller obtains a *fresh* instance each time it needs one (see
    `TransferMoneyUseCase`, which opens a second one to recover after losing
    the T5 race) -- this type is not itself reused across `async with`
    blocks.
    """

    accounts: AccountRepository
    transfers: TransferRepository
    idempotency: IdempotencyRepository

    @abstractmethod
    async def __aenter__(self) -> Self:
        """Begins the transaction and binds `accounts`/`transfers`/`idempotency` to it."""

    @abstractmethod
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Commits on a clean exit; rolls back if an exception propagated through the block."""
