from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.account_balance.adapters.outbound.repositories.sql.dbos.models import (
    EntryDbo,
    TransferDbo,
)
from modules.account_balance.application.gateways.transfer_repository import TransferRepository
from modules.account_balance.domain.identifiers import TransferId
from modules.account_balance.domain.posting import Posting
from modules.account_balance.domain.transfer import Transfer


class SqlTransferRepository(TransferRepository):
    """SQLAlchemy async adapter for `TransferRepository` (T9's sibling to `SqlAccountRepository`).
    Used only through `SqlTransferUnitOfWork` in this slice, so `session_factory` is always a
    shared, already-open session -- `add()` therefore only flushes, never commits (T3)."""

    def __init__(self, session_factory: Callable[[], AsyncSession]) -> None:
        self._session_factory = session_factory

    async def add(self, posting: Posting) -> None:
        async with self._session_factory() as session:
            # Two flushes, not one: `entries.transfer_id` has a FK to
            # `transfers.transfer_id`, and neither `TransferDbo` nor
            # `EntryDbo` declares an ORM `relationship()` between them (they
            # are plain column mappings, AO6) -- without one, the unit of
            # work has no dependency to sort by and may emit both inserts in
            # the same batch, in either order.
            session.add(TransferDbo.from_domain(posting.transfer))
            await session.flush()
            session.add_all(EntryDbo.from_domain(entry) for entry in posting.transfer.entries)
            await session.flush()

    async def get(self, transfer_id: TransferId) -> Transfer | None:
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
