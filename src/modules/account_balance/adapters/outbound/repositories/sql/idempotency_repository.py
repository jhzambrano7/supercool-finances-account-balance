from collections.abc import Callable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from modules.account_balance.adapters.outbound.repositories.sql.models import (
    IdempotencyRecordRow,
)
from modules.account_balance.application.gateways.idempotency_repository import (
    IdempotencyRecord,
    IdempotencyRecordConflictError,
    IdempotencyRepository,
)
from modules.account_balance.domain.identifiers import IdempotencyKey, OwnerId, TransferId

_IDEMPOTENCY_KEY_CONSTRAINT = "idempotency_records_pkey"


class SqlIdempotencyRepository(IdempotencyRepository):
    """SQLAlchemy async adapter for `IdempotencyRepository` (T3, T9's second

    sibling to `SqlAccountRepository`). Like `SqlTransferRepository`, used
    only through `SqlTransferUnitOfWork`'s shared session -- `add()` only
    flushes, so the T5 unique-violation surfaces before this attempt's
    transaction commits, not after.
    """

    def __init__(self, session_factory: Callable[[], AsyncSession]) -> None:
        self._session_factory = session_factory

    async def find_by_key(
        self, *, caller_id: OwnerId, idempotency_key: IdempotencyKey
    ) -> IdempotencyRecord | None:
        async with self._session_factory() as session:
            row = await session.get(IdempotencyRecordRow, (caller_id.value, idempotency_key.value))
            return _to_domain(row) if row is not None else None

    async def add(self, record: IdempotencyRecord) -> None:
        async with self._session_factory() as session:
            session.add(_to_row(record))
            try:
                await session.flush()
            except IntegrityError as exc:
                if not _violates_idempotency_constraint(exc):
                    raise
                raise IdempotencyRecordConflictError(
                    f"idempotency record already exists for caller={record.caller_id}, "
                    f"key={record.idempotency_key}"
                ) from exc


def _violates_idempotency_constraint(exc: IntegrityError) -> bool:
    """Narrows the catch in `add()` to T5's own constraint -- the composite

    primary key on `(caller_id, idempotency_key)` -- not any `IntegrityError`
    (mirrors AO4's `_violates_natural_key_constraint`).
    """
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None) == _IDEMPOTENCY_KEY_CONSTRAINT


def _to_row(record: IdempotencyRecord) -> IdempotencyRecordRow:
    return IdempotencyRecordRow(
        caller_id=record.caller_id.value,
        idempotency_key=record.idempotency_key.value,
        request_hash=record.request_hash,
        transfer_id=record.transfer_id.value,
        status=record.status,
        created_at=record.created_at,
    )


def _to_domain(row: IdempotencyRecordRow) -> IdempotencyRecord:
    return IdempotencyRecord(
        caller_id=OwnerId(row.caller_id),
        idempotency_key=IdempotencyKey(row.idempotency_key),
        request_hash=row.request_hash,
        transfer_id=TransferId(row.transfer_id),
        status=row.status,
        created_at=row.created_at,
    )
