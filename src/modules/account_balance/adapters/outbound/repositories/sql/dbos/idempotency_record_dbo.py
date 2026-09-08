from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from modules.account_balance.application.gateways.idempotency_repository import (
    IdempotencyRecord,
)
from modules.account_balance.domain.identifiers import IdempotencyKey, OwnerId, TransferId
from modules.shared.adapters.outbound.repositories.sql.base import Base


class IdempotencyRecordDbo(Base):
    """The persisted shape of `IdempotencyRecord` (T3, T9)."""

    __tablename__ = "idempotency_records"

    caller_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    transfer_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    @staticmethod
    def from_domain(record: IdempotencyRecord) -> IdempotencyRecordDbo:
        return IdempotencyRecordDbo(
            caller_id=record.caller_id.value,
            idempotency_key=record.idempotency_key.value,
            request_hash=record.request_hash,
            transfer_id=record.transfer_id.value,
            created_at=record.created_at,
        )

    def as_domain(self) -> IdempotencyRecord:
        return IdempotencyRecord(
            caller_id=OwnerId(self.caller_id),
            idempotency_key=IdempotencyKey(self.idempotency_key),
            request_hash=self.request_hash,
            transfer_id=TransferId(self.transfer_id),
            created_at=self.created_at,
        )
