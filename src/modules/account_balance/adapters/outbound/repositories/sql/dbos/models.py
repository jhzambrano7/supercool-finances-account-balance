from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from modules.account_balance.application.gateways.idempotency_repository import (
    IdempotencyRecord,
)
from modules.account_balance.domain.account import (
    Account,
    AccountPurpose,
    AccountStatus,
    AccountType,
)
from modules.account_balance.domain.entry import Entry, EntryDirection
from modules.account_balance.domain.identifiers import (
    AccountId,
    EntryId,
    IdempotencyKey,
    OwnerId,
    TransferId,
)
from modules.account_balance.domain.transfer import Transfer
from modules.shared.adapters.outbound.repositories.sql.base import Base
from modules.shared.domain.money import Currency, Money


class AccountDbo(Base):
    """The persisted shape of `Account` (AO6) — this mapping is the only place the domain's fields
    are flattened into columns; `Account` itself never imports SQLAlchemy."""

    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "purpose", "currency", name="uq_accounts_owner_purpose_currency"
        ),
    )

    account_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    account_type: Mapped[str] = mapped_column(String(10), nullable=False)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    balance_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    @staticmethod
    def from_domain(account: Account) -> AccountDbo:
        return AccountDbo(
            account_id=account.account_id.value,
            owner_id=account.owner_id.value,
            account_type=account.account_type.value,
            purpose=account.purpose.value,
            currency=account.currency.code,
            balance_amount=account.balance.amount,
            status=account.status.value,
            version=account.version,
        )

    def as_domain(self, *, balance_amount: int | None = None) -> Account:
        """`balance_amount` overrides the stored column -- a `SYSTEM` account's real balance is
        computed from its entries (T7), never read from this row; the caller passes that computed
        value in rather than this method reaching for entries itself (AO6, no SQL leaks here)."""
        currency = Currency(self.currency)
        return Account.reconstitute(
            account_id=AccountId(self.account_id),
            owner_id=OwnerId(self.owner_id),
            account_type=AccountType(self.account_type),
            purpose=AccountPurpose(self.purpose),
            currency=currency,
            balance=Money(
                self.balance_amount if balance_amount is None else balance_amount, currency
            ),
            status=AccountStatus(self.status),
            version=self.version,
        )


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


class IdempotencyRecordDbo(Base):
    """The persisted shape of `IdempotencyRecord` (T3, T9)."""

    __tablename__ = "idempotency_records"

    caller_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    transfer_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    @staticmethod
    def from_domain(record: IdempotencyRecord) -> IdempotencyRecordDbo:
        return IdempotencyRecordDbo(
            caller_id=record.caller_id.value,
            idempotency_key=record.idempotency_key.value,
            request_hash=record.request_hash,
            transfer_id=record.transfer_id.value,
            status=record.status,
            created_at=record.created_at,
        )

    def as_domain(self) -> IdempotencyRecord:
        return IdempotencyRecord(
            caller_id=OwnerId(self.caller_id),
            idempotency_key=IdempotencyKey(self.idempotency_key),
            request_hash=self.request_hash,
            transfer_id=TransferId(self.transfer_id),
            status=self.status,
            created_at=self.created_at,
        )
