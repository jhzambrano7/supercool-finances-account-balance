from collections.abc import Callable
from logging import Logger

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from modules.account_balance.adapters.outbound.repositories.sql.dbos.entry_dbo import EntryDbo
from modules.account_balance.adapters.outbound.repositories.sql.dbos.transfer_dbo import (
    TransferDbo,
)
from modules.account_balance.adapters.outbound.repositories.sql.queries.movement_cursor import (
    decode_movement_cursor,
    encode_movement_cursor,
)
from modules.account_balance.application.gateways.models.movement import Movement, MovementPage
from modules.account_balance.application.gateways.movement_repository import (
    MovementRepository,
    MovementRepositoryError,
)
from modules.account_balance.domain.entry import EntryDirection
from modules.account_balance.domain.identifiers import AccountId, EntryId, OwnerId, TransferId
from modules.shared.domain.money import Currency, Money


class SqlMovementRepository(MovementRepository):
    """SQLAlchemy async adapter for `MovementRepository`. Joins `entries` against `transfers` --
    neither declares an ORM `relationship()` (AO6, plain column mappings) -- since a movement's
    `counterparty_account_id` and `currency` live on the transfer row, not the entry's own
    (`EntryDbo` deliberately carries no `currency`, T9).
    """

    def __init__(self, logger: Logger, session_factory: Callable[[], AsyncSession]) -> None:
        self._logger = logger
        self._session_factory = session_factory

    async def find_by_account(
        self, *, account_id: AccountId, limit: int, cursor: str | None
    ) -> MovementPage:
        # Decoded before the session opens: a malformed cursor is the caller's own mistake, not a
        # database failure, and must propagate as InvalidMovementCursorError untouched by the
        # generic except below -- never wrapped as a MovementRepositoryError.
        cursor_bound = decode_movement_cursor(cursor) if cursor is not None else None
        try:
            async with self._session_factory() as session:
                query = (
                    select(EntryDbo, TransferDbo)
                    .join(TransferDbo, EntryDbo.transfer_id == TransferDbo.transfer_id)
                    .where(EntryDbo.account_id == account_id.value)
                )
                if cursor_bound is not None:
                    query = query.where(
                        tuple_(EntryDbo.occurred_at, EntryDbo.entry_id) < cursor_bound
                    )
                # limit + 1: fetching one extra row is how has_more is known without a second
                # COUNT query -- newest-first (T9's append-only ledger, read newest-first).
                query = query.order_by(EntryDbo.occurred_at.desc(), EntryDbo.entry_id.desc()).limit(
                    limit + 1
                )
                rows = (await session.execute(query)).all()
        except Exception as exc:
            self._logger.exception("unexpected error finding movements for account %s", account_id)
            raise MovementRepositoryError(
                operation="find_by_account",
                cause=exc,
                metadata={"account_id": str(account_id.value)},
            ) from exc

        has_more = len(rows) > limit
        page_rows = rows[:limit]
        items = tuple(_to_movement(entry, transfer) for entry, transfer in page_rows)
        next_cursor = (
            encode_movement_cursor(page_rows[-1][0].occurred_at, page_rows[-1][0].entry_id)
            if has_more
            else None
        )
        return MovementPage(items=items, next_cursor=next_cursor)


def _to_movement(entry: EntryDbo, transfer: TransferDbo) -> Movement:
    currency = Currency(transfer.currency)
    counterparty = (
        transfer.destination_account_id
        if entry.account_id == transfer.source_account_id
        else transfer.source_account_id
    )
    return Movement(
        entry_id=EntryId(entry.entry_id),
        transfer_id=TransferId(entry.transfer_id),
        direction=EntryDirection(entry.direction),
        amount=Money(entry.amount, currency),
        counterparty_account_id=AccountId(counterparty),
        requested_by=OwnerId(transfer.requested_by),
        occurred_at=entry.occurred_at,
    )
