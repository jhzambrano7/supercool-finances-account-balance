from collections.abc import Callable, Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from modules.account_balance.adapters.outbound.repositories.sql.models import EntryRow, TransferRow
from modules.account_balance.application.gateways.transfer_repository import (
    TransferAlreadyReversedConflictError,
    TransferRepository,
)
from modules.account_balance.domain.entry import Entry, EntryDirection
from modules.account_balance.domain.identifiers import (
    AccountId,
    EntryId,
    IdempotencyKey,
    OwnerId,
    TransferId,
)
from modules.account_balance.domain.posting import Posting
from modules.account_balance.domain.transfer import Transfer
from modules.shared.domain.money import Currency, Money

_REVERSES_CONSTRAINT = "uq_transfers_reverses"


class SqlTransferRepository(TransferRepository):
    """SQLAlchemy async adapter for `TransferRepository` (T9's sibling to

    `SqlAccountRepository`). Used only through `SqlTransferUnitOfWork` in
    this slice, so `session_factory` is always a shared, already-open
    session -- `add()` therefore only flushes, never commits (T3).
    """

    def __init__(self, session_factory: Callable[[], AsyncSession]) -> None:
        self._session_factory = session_factory

    async def add(self, posting: Posting) -> None:
        async with self._session_factory() as session:
            # Two flushes, not one: `entries.transfer_id` has a FK to
            # `transfers.transfer_id`, and neither `TransferRow` nor
            # `EntryRow` declares an ORM `relationship()` between them (they
            # are plain column mappings, AO6) -- without one, the unit of
            # work has no dependency to sort by and may emit both inserts in
            # the same batch, in either order.
            session.add(_transfer_to_row(posting.transfer))
            try:
                await session.flush()
            except IntegrityError as exc:
                if not _violates_reverses_constraint(exc):
                    raise
                raise TransferAlreadyReversedConflictError(
                    f"transfer {posting.transfer.reverses} already has a reversal"
                ) from exc
            session.add_all(_entry_to_row(entry) for entry in posting.transfer.entries)
            await session.flush()

    async def get(self, transfer_id: TransferId) -> Transfer | None:
        async with self._session_factory() as session:
            row = await session.get(TransferRow, transfer_id.value)
            if row is None:
                return None
            entry_rows = (
                await session.scalars(
                    select(EntryRow)
                    .where(EntryRow.transfer_id == transfer_id.value)
                    .order_by(EntryRow.occurred_at)
                )
            ).all()
            return _to_domain(row, entry_rows)


def _violates_reverses_constraint(exc: IntegrityError) -> bool:
    """Narrows the catch in `add()` to R4's own partial unique index

    (migration `cf910528c0f8`), not any `IntegrityError` -- mirrors AO4's
    `_violates_natural_key_constraint` and T5's
    `_violates_idempotency_constraint`.
    """
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None) == _REVERSES_CONSTRAINT


def _transfer_to_row(transfer: Transfer) -> TransferRow:
    return TransferRow(
        transfer_id=transfer.transfer_id.value,
        source_account_id=transfer.source_account_id.value,
        destination_account_id=transfer.destination_account_id.value,
        amount=transfer.amount.amount,
        currency=str(transfer.amount.currency),
        requested_by=transfer.requested_by.value,
        idempotency_key=transfer.idempotency_key.value,
        occurred_at=transfer.occurred_at,
        reverses=transfer.reverses.value if transfer.reverses is not None else None,
    )


def _entry_to_row(entry: Entry) -> EntryRow:
    return EntryRow(
        entry_id=entry.entry_id.value,
        transfer_id=entry.transfer_id.value,
        account_id=entry.account_id.value,
        direction=entry.direction.value,
        amount=entry.amount.amount,
        occurred_at=entry.occurred_at,
    )


def _to_domain(row: TransferRow, entry_rows: Sequence[EntryRow]) -> Transfer:
    currency = Currency(row.currency)
    entries = tuple(
        Entry(
            entry_id=EntryId(entry_row.entry_id),
            transfer_id=TransferId(entry_row.transfer_id),
            account_id=AccountId(entry_row.account_id),
            direction=EntryDirection(entry_row.direction),
            amount=Money(entry_row.amount, currency),
            occurred_at=entry_row.occurred_at,
        )
        for entry_row in entry_rows
    )
    return Transfer(
        transfer_id=TransferId(row.transfer_id),
        source_account_id=AccountId(row.source_account_id),
        destination_account_id=AccountId(row.destination_account_id),
        amount=Money(row.amount, currency),
        requested_by=OwnerId(row.requested_by),
        idempotency_key=IdempotencyKey(row.idempotency_key),
        occurred_at=row.occurred_at,
        entries=entries,
        reverses=TransferId(row.reverses) if row.reverses is not None else None,
    )
