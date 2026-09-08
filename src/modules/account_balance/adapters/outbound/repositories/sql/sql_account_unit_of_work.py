from collections.abc import Callable
from logging import Logger
from types import TracebackType
from typing import Self, cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from modules.account_balance.adapters.outbound.repositories.sql.kept_open_session import (
    KeptOpenSession,
)
from modules.account_balance.adapters.outbound.repositories.sql.sql_account_repository import (
    SqlAccountRepository,
)
from modules.account_balance.application.gateways.account_unit_of_work import AccountUnitOfWork


class SqlAccountUnitOfWork(AccountUnitOfWork):
    """SQLAlchemy async adapter for `AccountUnitOfWork` (§7.2).

    Opens one session per `async with` block, binds `accounts` to it via `KeptOpenSession`, and
    commits once on a clean exit or rolls back once if an exception propagated out -- mirrors
    `SqlTransferUnitOfWork` exactly, minus the two repositories a lifecycle operation has no use
    for.
    """

    def __init__(self, logger: Logger, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._logger = logger
        self._session_maker = session_factory
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> Self:
        session = self._session_maker()
        self._session = session
        bound = cast(Callable[[], AsyncSession], lambda: KeptOpenSession(session))
        self.accounts = SqlAccountRepository(self._logger, bound)
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
