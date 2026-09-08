from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from modules.account_balance.adapters.outbound.repositories.sql.dbos.entry_dbo import EntryDbo
from modules.account_balance.domain.identifiers import (
    AccountId,
    IdempotencyKey,
    OwnerId,
    TransferId,
)
from modules.account_balance.domain.transfer import Transfer
from modules.shared.adapters.outbound.repositories.sql.base import Base
from modules.shared.domain.money import Currency, Money


class TransferDbo(Base):
    """The persisted shape of `Transfer` (T9) -- entries are a separate table, mapped by
    `EntryDbo`; neither declares an ORM `relationship()` between them (AO6, plain column
    mappings), so `as_domain()` takes the already-loaded entries as a parameter."""

    __tablename__ = "transfers"

    transfer_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    source_account_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    destination_account_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    requested_by: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reverses: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)

    @staticmethod
    def from_domain(transfer: Transfer) -> TransferDbo:
        return TransferDbo(
            transfer_id=transfer.transfer_id.value,
            source_account_id=transfer.source_account_id.value,
            destination_account_id=transfer.destination_account_id.value,
            amount=transfer.amount.amount,
            currency=transfer.amount.currency.code,
            requested_by=transfer.requested_by.value,
            idempotency_key=transfer.idempotency_key.value,
            occurred_at=transfer.occurred_at,
            reverses=transfer.reverses.value if transfer.reverses is not None else None,
        )

    def as_domain(self, entries: Sequence[EntryDbo]) -> Transfer:
        currency = Currency(self.currency)
        return Transfer(
            transfer_id=TransferId(self.transfer_id),
            source_account_id=AccountId(self.source_account_id),
            destination_account_id=AccountId(self.destination_account_id),
            amount=Money(self.amount, currency),
            requested_by=OwnerId(self.requested_by),
            idempotency_key=IdempotencyKey(self.idempotency_key),
            occurred_at=self.occurred_at,
            entries=tuple(entry_dbo.as_domain(currency) for entry_dbo in entries),
            reverses=TransferId(self.reverses) if self.reverses is not None else None,
        )
