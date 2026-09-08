from collections.abc import Callable
from logging import Logger

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from modules.account_balance.adapters.outbound.repositories.sql.dbos.idempotency_record_dbo import (
    IdempotencyRecordDbo,
)
from modules.account_balance.application.gateways.idempotency_repository import (
    IdempotencyRecord,
    IdempotencyRecordConflictError,
    IdempotencyRepository,
    IdempotencyRepositoryError,
)
from modules.account_balance.domain.identifiers import IdempotencyKey, OwnerId

_IDEMPOTENCY_KEY_CONSTRAINT = "idempotency_records_pkey"


class SqlIdempotencyRepository(IdempotencyRepository):
    """SQLAlchemy async adapter for `IdempotencyRepository` (T3, T9's second sibling to
    `SqlAccountRepository`). Like `SqlTransferRepository`, used only through
    `SqlTransferUnitOfWork`'s shared session -- `add()` only flushes, so the T5 unique-violation
    surfaces before this attempt's transaction commits, not after."""

    def __init__(self, logger: Logger, session_factory: Callable[[], AsyncSession]) -> None:
        self._logger = logger
        self._session_factory = session_factory

    async def find_by_key(
        self, *, caller_id: OwnerId, idempotency_key: IdempotencyKey
    ) -> IdempotencyRecord | None:
        try:
            async with self._session_factory() as session:
                dbo = await session.get(
                    IdempotencyRecordDbo, (caller_id.value, idempotency_key.value)
                )
                return dbo.as_domain() if dbo is not None else None
        except Exception as exc:
            self._logger.exception(
                "unexpected error finding an idempotency record for caller=%s, key=%s",
                caller_id,
                idempotency_key,
            )
            raise IdempotencyRepositoryError(
                operation="find_by_key",
                cause=exc,
                metadata={
                    "caller_id": str(caller_id.value),
                    "idempotency_key": str(idempotency_key.value),
                },
            ) from exc

    async def add(self, record: IdempotencyRecord) -> None:
        try:
            async with self._session_factory() as session:
                session.add(IdempotencyRecordDbo.from_domain(record))
                await session.flush()
        except IntegrityError as exc:
            # Logged unconditionally -- every IntegrityError this adapter sees
            # is recorded, including the recognized T5 conflict below, so the
            # log is a complete audit trail of integrity violations rather than
            # only the ones this adapter fails to explain.
            self._logger.exception(
                "integrity error adding an idempotency record for caller=%s, key=%s",
                record.caller_id,
                record.idempotency_key,
            )
            if not _violates_idempotency_constraint(exc):
                raise IdempotencyRepositoryError(
                    operation="add",
                    cause=exc,
                    metadata={
                        "caller_id": str(record.caller_id.value),
                        "idempotency_key": str(record.idempotency_key.value),
                    },
                ) from exc
            raise IdempotencyRecordConflictError(
                caller_id=record.caller_id, idempotency_key=record.idempotency_key
            ) from exc
        except Exception as exc:
            self._logger.exception(
                "unexpected error adding an idempotency record for caller=%s, key=%s",
                record.caller_id,
                record.idempotency_key,
            )
            raise IdempotencyRepositoryError(
                operation="add",
                cause=exc,
                metadata={
                    "caller_id": str(record.caller_id.value),
                    "idempotency_key": str(record.idempotency_key.value),
                },
            ) from exc


def _violates_idempotency_constraint(exc: IntegrityError) -> bool:
    """Narrows the catch in `add()` to T5's own constraint -- the composite primary key on
    `(caller_id, idempotency_key)` -- not any `IntegrityError` (mirrors AO4's
    `_violates_natural_key_constraint`)."""
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None) == _IDEMPOTENCY_KEY_CONSTRAINT
