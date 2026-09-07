from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for account_balance's SQL adapters — Alembic's `target_metadata`."""


class AccountRow(Base):
    """The persisted shape of `Account` (AO6) — this mapping is the only place

    the domain's fields are flattened into columns; `Account` itself never
    imports SQLAlchemy.
    """

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


class TransferRow(Base):
    """The persisted shape of a `Transfer` (T9's sibling table to `accounts`).

    `reverses` is nullable and always `NULL` in this slice -- reversal is
    out of scope (spec Scope: "Out"); the column exists now because it is
    already part of the domain `Transfer`'s own field set, not something
    the next spec needs to add via its own migration.
    """

    __tablename__ = "transfers"

    transfer_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    source_account_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.account_id"), nullable=False
    )
    destination_account_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.account_id"), nullable=False
    )
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    requested_by: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reverses: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transfers.transfer_id"), nullable=True
    )


class EntryRow(Base):
    """The persisted shape of one `Entry` leg (T9's sibling table).

    `account_id` is indexed implicitly by the FK, which is also the column
    T7's `SUM(signed_amount)` query for a `SYSTEM` account's balance filters
    on.
    """

    __tablename__ = "entries"

    entry_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    transfer_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transfers.transfer_id"), nullable=False
    )
    account_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.account_id"), nullable=False
    )
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IdempotencyRecordRow(Base):
    """The durable idempotency row (T3, PRD §6.1).

    `(caller_id, idempotency_key)` is the primary key directly -- no
    surrogate id -- since that pair *is* the row's identity; Postgres names
    a composite primary key's own unique index `idempotency_records_pkey` by
    default, which is the constraint name the T5 race-catch narrows to
    (mirroring AO4's `uq_accounts_owner_purpose_currency` narrowing).
    """

    __tablename__ = "idempotency_records"

    caller_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    transfer_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transfers.transfer_id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
