from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from modules.account_balance.adapters.outbound.repositories.sql.models import AccountRow
from modules.account_balance.application.gateways.account_repository import (
    AccountNaturalKeyConflictError,
    AccountRepository,
)
from modules.account_balance.domain.account import (
    Account,
    AccountPurpose,
    AccountStatus,
    AccountType,
)
from modules.account_balance.domain.identifiers import AccountId, OwnerId
from modules.shared.domain.money import Currency, Money


class SqlAccountRepository(AccountRepository):
    """SQLAlchemy async adapter for `AccountRepository` (AO5, AO6).

    Each method opens and closes its own session. There is no
    request-spanning unit of work in this first slice — every operation here
    is exactly one statement, so nothing is lost by not sharing a
    transaction across them.
    """

    def __init__(self, session_factory: Callable[[], AsyncSession]) -> None:
        self._session_factory = session_factory

    async def find_by_natural_key(
        self, *, owner_id: OwnerId, purpose: AccountPurpose, currency: Currency
    ) -> Account | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(AccountRow).where(
                    AccountRow.owner_id == owner_id.value,
                    AccountRow.purpose == purpose.value,
                    AccountRow.currency == str(currency),
                )
            )
            return _to_domain(row) if row is not None else None

    async def add(self, account: Account) -> None:
        row = _to_row(account)
        async with self._session_factory() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise AccountNaturalKeyConflictError(
                    f"account already exists for natural key (owner={account.owner_id}, "
                    f"purpose={account.purpose.value}, currency={account.currency})"
                ) from exc

    async def get(self, account_id: AccountId) -> Account | None:
        async with self._session_factory() as session:
            row = await session.get(AccountRow, account_id.value)
            return _to_domain(row) if row is not None else None


def _to_row(account: Account) -> AccountRow:
    return AccountRow(
        account_id=account.account_id.value,
        owner_id=account.owner_id.value,
        account_type=account.account_type.value,
        purpose=account.purpose.value,
        currency=str(account.currency),
        balance_amount=account.balance.amount,
        status=account.status.value,
        version=account.version,
    )


def _to_domain(row: AccountRow) -> Account:
    """Always `reconstitute()`, never `open()` — this row may already hold a

    non-zero balance (domain spec: "Loading a previously-persisted, non-zero
    account MUST use reconstitute(), never open()").
    """
    currency = Currency(row.currency)
    return Account.reconstitute(
        account_id=AccountId(row.account_id),
        owner_id=OwnerId(row.owner_id),
        account_type=AccountType(row.account_type),
        purpose=AccountPurpose(row.purpose),
        currency=currency,
        balance=Money(row.balance_amount, currency),
        status=AccountStatus(row.status),
        version=row.version,
    )
