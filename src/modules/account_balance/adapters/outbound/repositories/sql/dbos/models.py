from uuid import UUID

from sqlalchemy import BigInteger, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from modules.account_balance.domain.account import (
    Account,
    AccountPurpose,
    AccountStatus,
    AccountType,
)
from modules.account_balance.domain.identifiers import AccountId, OwnerId
from modules.shared.adapters.outbound.repositories.sql.base import Base
from modules.shared.domain.money import Currency, Money


class AccountDbo(Base):
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

    def as_domain(self) -> Account:
        return Account.reconstitute(
            account_id=AccountId(self.account_id),
            owner_id=OwnerId(self.owner_id),
            account_type=AccountType(self.account_type),
            purpose=AccountPurpose(self.purpose),
            currency=Currency(self.currency),
            balance=Money(self.balance_amount, Currency(self.currency)),
            status=AccountStatus(self.status),
            version=self.version,
        )
