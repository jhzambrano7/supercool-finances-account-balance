from collections.abc import Callable
from logging import Logger

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from modules.account_balance.adapters.outbound.repositories.sql.dbos.entry_dbo import EntryDbo
from modules.account_balance.adapters.outbound.repositories.sql.dbos.transfer_dbo import (
    TransferDbo,
)
from modules.account_balance.application.gateways.transfer_repository import (
    TransferAlreadyReversedConflictError,
    TransferRepository,
    TransferRepositoryError,
)
from modules.account_balance.domain.identifiers import TransferId
from modules.account_balance.domain.transfer import Transfer

_REVERSES_CONSTRAINT = "uq_transfers_reverses"


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
        except IntegrityError as exc:
            # Logged unconditionally -- every IntegrityError this adapter sees
            # is recorded, including the recognized R4 conflict below, so the
            # log is a complete audit trail of integrity violations rather than
            # only the ones this adapter fails to explain.
            self._logger.exception("integrity error adding transfer %s", transfer.transfer_id)
            if not _violates_reverses_constraint(exc):
                raise TransferRepositoryError(
                    operation="add",
                    cause=exc,
                    metadata={"transfer_id": str(transfer.transfer_id.value)},
                ) from exc
            if transfer.reverses is None:  # pragma: no cover -- defensive: the index only
                # applies to rows with reverses IS NOT NULL, so this constraint cannot fire
                # for an ordinary (non-reversal) transfer.
                raise TransferRepositoryError(
                    operation="add",
                    cause=exc,
                    metadata={"transfer_id": str(transfer.transfer_id.value)},
                ) from exc
            raise TransferAlreadyReversedConflictError(
                original_transfer_id=transfer.reverses
            ) from exc
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


def _violates_reverses_constraint(exc: IntegrityError) -> bool:
    """Narrows the catch in `add()` to R4's own partial unique index (migration
    `cf910528c0f8`), not any `IntegrityError` -- mirrors AO4's
    `_violates_natural_key_constraint` and T5's `_violates_idempotency_constraint`."""
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None) == _REVERSES_CONSTRAINT
