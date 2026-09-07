from collections.abc import Callable

from sqlalchemy import case, func, select
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from modules.account_balance.adapters.outbound.repositories.sql.models import AccountRow, EntryRow
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
from modules.account_balance.domain.entry import EntryDirection
from modules.account_balance.domain.identifiers import AccountId, OwnerId
from modules.shared.domain.money import Currency, Money


class SqlAccountRepository(AccountRepository):
    """SQLAlchemy async adapter for `AccountRepository` (AO5, AO6, T9).

    Every method asks `session_factory` for a session rather than holding
    one itself. `account-opening`'s own usage passes an `async_sessionmaker`
    that opens and closes a fresh session per call -- fine there, since each
    of its operations is exactly one statement. `TransferMoneyUseCase`
    instead constructs this class with a factory that keeps handing back the
    *same*, already-open session (see `SqlTransferUnitOfWork`) so
    `get_for_update`, `update` and the sibling transfer/idempotency
    repositories all share one transaction and the lock the first call takes
    survives until that transaction ends (T3, T6) -- neither this class's
    code nor its abstract signature needs to know which case it is in.
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
                if not _violates_natural_key_constraint(exc):
                    raise
                raise AccountNaturalKeyConflictError(
                    f"account already exists for natural key (owner={account.owner_id}, "
                    f"purpose={account.purpose.value}, currency={account.currency})"
                ) from exc

    async def get(self, account_id: AccountId) -> Account | None:
        async with self._session_factory() as session:
            row = await session.get(AccountRow, account_id.value)
            if row is None:
                return None
            if AccountType(row.account_type).is_system():
                # T7: a SYSTEM account's balance is never read from its
                # stored column -- computed as SUM(signed entries) instead.
                balance_amount = await _system_balance(session, account_id)
                return _to_domain(row, balance_amount=balance_amount)
            return _to_domain(row)

    async def get_for_update(self, account_id: AccountId) -> Account | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(AccountRow)
                .where(
                    AccountRow.account_id == account_id.value,
                    AccountRow.account_type == AccountType.USER.value,
                )
                .with_for_update()
            )
            return _to_domain(row) if row is not None else None

    async def update(self, account: Account) -> None:
        async with self._session_factory() as session:
            await session.execute(
                sa_update(AccountRow)
                .where(AccountRow.account_id == account.account_id.value)
                .values(balance_amount=account.balance.amount, version=account.version)
            )


_NATURAL_KEY_CONSTRAINT = "uq_accounts_owner_purpose_currency"


def _violates_natural_key_constraint(exc: IntegrityError) -> bool:
    """Narrows the catch in `add()` to AO4's own constraint (migration
    `38ec8c622b3f`), not any `IntegrityError` -- a future constraint on this
    table must surface as itself, not get silently relabelled as a natural-
    key conflict the use case would then mishandle.
    """
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None) == _NATURAL_KEY_CONSTRAINT


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


def _to_domain(row: AccountRow, *, balance_amount: int | None = None) -> Account:
    """Always `reconstitute()`, never `open()` — this row may already hold a

    non-zero balance (domain spec: "Loading a previously-persisted, non-zero
    account MUST use reconstitute(), never open()").

    `balance_amount`, when given, overrides `row.balance_amount` — the T7
    path for a `SYSTEM` account, whose stored column is never the answer.
    """
    currency = Currency(row.currency)
    amount = row.balance_amount if balance_amount is None else balance_amount
    return Account.reconstitute(
        account_id=AccountId(row.account_id),
        owner_id=OwnerId(row.owner_id),
        account_type=AccountType(row.account_type),
        purpose=AccountPurpose(row.purpose),
        currency=currency,
        balance=Money(amount, currency),
        status=AccountStatus(row.status),
        version=row.version,
    )


async def _system_balance(session: AsyncSession, account_id: AccountId) -> int:
    """T7: a `SYSTEM` account's balance is `SUM(signed entries)`, computed on

    demand, never a maintained column. `signed_amount`'s sign convention
    (`CREDIT` positive, `DEBIT` negative) mirrors `Entry.signed_amount` --
    the one place a sign is derived from direction (domain D2) -- so this
    query never disagrees with what the domain itself would compute from
    the same rows.
    """
    signed = case(
        (EntryRow.direction == EntryDirection.CREDIT.value, EntryRow.amount),
        else_=-EntryRow.amount,
    )
    total = await session.scalar(
        select(func.coalesce(func.sum(signed), 0)).where(EntryRow.account_id == account_id.value)
    )
    # `coalesce(..., 0)` guarantees a row and a non-NULL value whenever the
    # query runs at all -- `None` here would mean the query itself never
    # executed, which `session.scalar` cannot actually produce.
    assert total is not None
    return int(total)
