from collections.abc import Callable
from types import TracebackType
from typing import Self, cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from modules.account_balance.adapters.outbound.repositories.sql.account_repository import (
    SqlAccountRepository,
)
from modules.account_balance.adapters.outbound.repositories.sql.idempotency_repository import (
    SqlIdempotencyRepository,
)
from modules.account_balance.adapters.outbound.repositories.sql.transfer_repository import (
    SqlTransferRepository,
)
from modules.account_balance.application.gateways.unit_of_work import TransferUnitOfWork


class _KeptOpenSession:
    """An `async with`-able wrapper that hands back an already-open session

    without closing it on exit.

    `SqlAccountRepository`, `SqlTransferRepository` and
    `SqlIdempotencyRepository` all follow the same shape: every method does
    `async with self._session_factory() as session: ...`. Passing a real
    `async_sessionmaker` (as `account-opening`'s own usage does) gives each
    call a fresh, independently-committed session -- fine for a single
    statement. Passing `lambda: _KeptOpenSession(session)` instead makes
    every one of those `async with` blocks reuse the *same* session without
    ending its transaction early, which is what lets `get_for_update`'s lock
    survive from acquisition through the final `update()`/`add()` calls and
    into this unit of work's own commit (T3, T6) -- none of those classes'
    existing code has to know which case it is in.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class SqlTransferUnitOfWork(TransferUnitOfWork):
    """SQLAlchemy async adapter for `TransferUnitOfWork` (T3, T9).

    Opens one session per `async with` block, binds `accounts`, `transfers`
    and `idempotency` to it via `_KeptOpenSession`, and commits once on a
    clean exit or rolls back once if an exception propagated out -- the use
    case, not any individual repository call, decides which (design §5.3).
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_maker = session_factory
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> Self:
        session = self._session_maker()
        self._session = session
        bound = cast(Callable[[], AsyncSession], lambda: _KeptOpenSession(session))
        self.accounts = SqlAccountRepository(bound)
        self.transfers = SqlTransferRepository(bound)
        self.idempotency = SqlIdempotencyRepository(bound)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self._session is not None
        try:
            if exc_type is None:
                await self._session.commit()
            else:
                await self._session.rollback()
        finally:
            await self._session.close()
            self._session = None
