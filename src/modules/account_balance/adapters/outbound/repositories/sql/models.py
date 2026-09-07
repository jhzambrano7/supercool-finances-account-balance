from uuid import UUID

from sqlalchemy import BigInteger, Integer, String, UniqueConstraint
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
