from collections.abc import Callable
from logging import Logger
from typing import override

from sqlalchemy import case, func, select
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from modules.account_balance.adapters.outbound.repositories.sql.dbos.models import (
    AccountDbo,
    EntryDbo,
)
from modules.account_balance.adapters.outbound.repositories.sql.queries.account_criteria import (
    find_account_criteria_to_sql_query,
)
from modules.account_balance.application.gateways.account_repository import (
    AccountAlreadyExistsError,
    AccountRepository,
    AccountRepositoryError,
)
from modules.account_balance.application.gateways.models.find_accounts_criteria import (
    FindAccountCriteria,
)
from modules.account_balance.domain.account import Account, AccountType, UserAccount
from modules.account_balance.domain.entry import EntryDirection
from modules.account_balance.domain.identifiers import AccountId

_NATURAL_KEY_CONSTRAINT = "uq_accounts_owner_purpose_currency"


class SqlAccountRepository(AccountRepository):
    """SQLAlchemy async adapter for `AccountRepository` (AO5, AO6).

    Each method opens and closes its own session. There is no
    request-spanning unit of work in this first slice — every operation here
    is exactly one statement, so nothing is lost by not sharing a
    transaction across them.
    """

    def __init__(self, logger: Logger, session_factory: Callable[[], AsyncSession]) -> None:
        self._logger = logger
        self._session_factory = session_factory

    @override
    async def find(self, *, criteria: FindAccountCriteria) -> Account | None:
        try:
            async with self._session_factory() as session:
                dbo = await session.scalar(find_account_criteria_to_sql_query(criteria))
                if dbo is None:
                    return None
                if AccountType(dbo.account_type).is_system():
                    balance_amount = await _system_balance(session, dbo.account_id)
                    return dbo.as_domain(balance_amount=balance_amount)
                return dbo.as_domain()
        except Exception as exc:
            self._logger.exception("unexpected error finding an account for %r", criteria)
            raise AccountRepositoryError(
                operation="find", cause=exc, metadata={"criteria": repr(criteria)}
            ) from exc

    @override
    async def add(self, account: Account) -> None:
        # AO1: only `AccountRegister` calls `add()`, and it never opens a
        # `SYSTEM` account -- those rows exist only via the migration's own
        # seed insert (T8), never through this port.
        assert isinstance(account, UserAccount), "add() is only ever called with a USER account"
        dbo = AccountDbo.from_domain(account)
        try:
            async with self._session_factory() as session:
                session.add(dbo)
                await session.commit()
        except IntegrityError as exc:
            # Logged unconditionally -- every IntegrityError this adapter
            # sees is recorded, including the recognized AO4 conflict below,
            # so the log is a complete audit trail of integrity violations
            # rather than only the ones this adapter fails to explain.
            self._logger.exception("integrity error adding account %s", account.account_id)
            if _violates_natural_key_constraint(exc):
                raise AccountAlreadyExistsError(
                    owner_id=account.owner_id,
                    purpose=account.purpose,
                    currency=account.currency,
                ) from exc
            raise AccountRepositoryError(
                operation="add", cause=exc, metadata={"account_id": str(account.account_id.value)}
            ) from exc
        except Exception as exc:
            self._logger.exception("unexpected error adding account %s", account.account_id)
            raise AccountRepositoryError(
                operation="add", cause=exc, metadata={"account_id": str(account.account_id.value)}
            ) from exc

    @override
    async def get_for_update(self, account_id: AccountId) -> UserAccount | None:
        try:
            async with self._session_factory() as session:
                dbo = await session.scalar(
                    select(AccountDbo)
                    .where(
                        AccountDbo.account_id == account_id.value,
                        # T6, T7: a SYSTEM account is never locked -- excluded
                        # here rather than left to the caller to avoid by
                        # convention alone.
                        AccountDbo.account_type == AccountType.USER.value,
                    )
                    .with_for_update()
                )
                if dbo is None:
                    return None
                account = dbo.as_domain()
                assert isinstance(account, UserAccount), "excluded SYSTEM rows in the WHERE clause"
                return account
        except Exception as exc:
            self._logger.exception("unexpected error locking account %s", account_id)
            raise AccountRepositoryError(
                operation="get_for_update",
                cause=exc,
                metadata={"account_id": str(account_id.value)},
            ) from exc

    @override
    async def update(self, account: UserAccount) -> None:
        try:
            async with self._session_factory() as session:
                await session.execute(
                    sa_update(AccountDbo)
                    .where(AccountDbo.account_id == account.account_id.value)
                    .values(balance_amount=account.balance.amount, version=account.version)
                )
                await session.flush()
        except Exception as exc:
            self._logger.exception("unexpected error updating account %s", account.account_id)
            raise AccountRepositoryError(
                operation="update",
                cause=exc,
                metadata={"account_id": str(account.account_id.value)},
            ) from exc


async def _system_balance(session: AsyncSession, account_id: object) -> int:
    """T7: a `SYSTEM` account's balance is `SUM(signed entries)`, computed on every read -- the
    stored `balance_amount` column is seeded at zero (migration `5bf582a92358`) and never written
    to again for a `SYSTEM` row from this slice on."""
    signed = case(
        (EntryDbo.direction == EntryDirection.CREDIT.value, EntryDbo.amount),
        else_=-EntryDbo.amount,
    )
    total = await session.scalar(
        select(func.coalesce(func.sum(signed), 0)).where(EntryDbo.account_id == account_id)
    )
    return int(total or 0)


def _violates_natural_key_constraint(exc: IntegrityError) -> bool:
    """Narrows the catch in `add()` to AO4's own constraint (migration
    `38ec8c622b3f`), not any `IntegrityError` -- a future constraint on this
    table must surface as itself, not get silently relabelled as a natural-
    key conflict the use case would then mishandle.
    """
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None) == _NATURAL_KEY_CONSTRAINT
