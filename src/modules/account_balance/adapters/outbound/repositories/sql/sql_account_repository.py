from collections.abc import Callable
from logging import Logger
from typing import override

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from modules.account_balance.adapters.outbound.repositories.sql.dbos.models import AccountDbo
from modules.account_balance.adapters.outbound.repositories.sql.queries.account_criteria import (
    find_account_criteria_to_sql_query,
)
from modules.account_balance.application.gateways.account_repository import (
    AccountNaturalKeyConflictError,
    AccountRepository,
    AccountRepositoryError,
)
from modules.account_balance.application.gateways.models.find_accounts_criteria import (
    FindAccountCriteria,
)
from modules.account_balance.domain.account import Account

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
                return dbo.as_domain() if dbo is not None else None
        except Exception as exc:
            self._logger.exception("unexpected error finding an account for %r", criteria)
            raise AccountRepositoryError(
                operation="find", cause=exc, metadata={"criteria": repr(criteria)}
            ) from exc

    @override
    async def add(self, account: Account) -> None:
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
                raise AccountNaturalKeyConflictError(
                    f"account already exists for natural key (owner={account.owner_id}, "
                    f"purpose={account.purpose.value}, currency={account.currency})"
                ) from exc
            raise AccountRepositoryError(
                operation="add", cause=exc, metadata={"account_id": str(account.account_id.value)}
            ) from exc
        except Exception as exc:
            self._logger.exception("unexpected error adding account %s", account.account_id)
            raise AccountRepositoryError(
                operation="add", cause=exc, metadata={"account_id": str(account.account_id.value)}
            ) from exc


def _violates_natural_key_constraint(exc: IntegrityError) -> bool:
    """Narrows the catch in `add()` to AO4's own constraint (migration
    `38ec8c622b3f`), not any `IntegrityError` -- a future constraint on this
    table must surface as itself, not get silently relabelled as a natural-
    key conflict the use case would then mishandle.
    """
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None) == _NATURAL_KEY_CONSTRAINT
