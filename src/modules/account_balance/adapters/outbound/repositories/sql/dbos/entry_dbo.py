from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from modules.account_balance.domain.entry import Entry, EntryDirection
from modules.account_balance.domain.identifiers import AccountId, EntryId, TransferId
from modules.shared.adapters.outbound.repositories.sql.base import Base
from modules.shared.domain.money import Currency, Money


class EntryDbo(Base):
    """The persisted shape of one `Entry` leg -- `amount` is bare (BigInteger); `currency` comes
    from the owning `Transfer`, not this row -- an entry never carries one of its own (T9)."""

    __tablename__ = "entries"

    entry_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    transfer_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    account_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    @staticmethod
    def from_domain(entry: Entry) -> EntryDbo:
        return EntryDbo(
            entry_id=entry.entry_id.value,
            transfer_id=entry.transfer_id.value,
            account_id=entry.account_id.value,
            direction=entry.direction.value,
            amount=entry.amount.amount,
            occurred_at=entry.occurred_at,
        )

    def as_domain(self, currency: Currency) -> Entry:
        return Entry(
            entry_id=EntryId(self.entry_id),
            transfer_id=TransferId(self.transfer_id),
            account_id=AccountId(self.account_id),
            direction=EntryDirection(self.direction),
            amount=Money(self.amount, currency),
            occurred_at=self.occurred_at,
        )
