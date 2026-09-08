from collections.abc import Callable
from logging import Logger

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.account_balance.adapters.outbound.repositories.sql.dbos.entry_dbo import EntryDbo
from modules.account_balance.adapters.outbound.repositories.sql.dbos.transfer_dbo import (
    TransferDbo,
)
from modules.account_balance.application.gateways.transfer_repository import (
    TransferRepository,
    TransferRepositoryError,
)
from modules.account_balance.domain.identifiers import TransferId
from modules.account_balance.domain.transfer import Transfer


class SqlTransferRepository(TransferRepository):
    """SQLAlchemy async adapter for `TransferRepository` (T9's sibling to `SqlAccountRepository`).
    Used only through `SqlTransferUnitOfWork` in this slice, so `session_factory` is always a
    shared, already-open session -- `add()` therefore only flushes, never commits (T3)."""

    def __init__(self, logger: Logger, session_factory: Callable[[], AsyncSession]) -> None:
        self._logger = logger
        self._session_factory = session_factory

    async def add(self, transfer: Transfer) -> None:
        try:
            async with self._session_factory() as session:
                # Two flushes, not one: `entries.transfer_id` has a FK to
                # `transfers.transfer_id`, and neither `TransferDbo` nor
                # `EntryDbo` declares an ORM `relationship()` between them (they
                # are plain column mappings, AO6) -- without one, the unit of
                # work has no dependency to sort by and may emit both inserts in
                # the same batch, in either order.
                session.add(TransferDbo.from_domain(transfer))
                await session.flush()
                session.add_all(EntryDbo.from_domain(entry) for entry in transfer.entries)
                await session.flush()
        except Exception as exc:
            self._logger.exception("unexpected error adding transfer %s", transfer.transfer_id)
            raise TransferRepositoryError(
                operation="add",
                cause=exc,
                metadata={"transfer_id": str(transfer.transfer_id.value)},
            ) from exc

    async def get(self, transfer_id: TransferId) -> Transfer | None:
        try:
            async with self._session_factory() as session:
                dbo = await session.get(TransferDbo, transfer_id.value)
                if dbo is None:
                    return None
                entry_dbos = (
                    await session.scalars(
                        select(EntryDbo)
                        .where(EntryDbo.transfer_id == transfer_id.value)
                        .order_by(EntryDbo.occurred_at)
                    )
                ).all()
                return dbo.as_domain(entry_dbos)
        except Exception as exc:
            self._logger.exception("unexpected error getting transfer %s", transfer_id)
            raise TransferRepositoryError(
                operation="get",
                cause=exc,
                metadata={"transfer_id": str(transfer_id.value)},
            ) from exc
