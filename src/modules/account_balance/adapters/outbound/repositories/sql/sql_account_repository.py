from collections.abc import Callable
from logging import Logger
from typing import cast, override

from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from modules.account_balance.adapters.outbound.repositories.sql.dbos.account_dbo import AccountDbo
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
from modules.account_balance.domain.identifiers import AccountId, OwnerId

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
    async def get_many_for_update(
        self, account_ids: tuple[AccountId, ...]
    ) -> tuple[UserAccount, ...]:
        try:
            async with self._session_factory() as session:
                dbos = (
                    await session.scalars(
                        select(AccountDbo)
                        .where(
                            AccountDbo.account_id.in_([id_.value for id_ in account_ids]),
                            # T6, T7: a SYSTEM account is never locked.
                            AccountDbo.account_type == AccountType.USER.value,
                        )
                        # Stated, not inherited: Postgres takes the row locks in
                        # the order this query produces them, so the ORDER BY is
                        # what makes the lock order deterministic (T6). One
                        # statement rather than N is also what keeps that order
                        # a property of the query instead of of the caller's
                        # loop.
                        .order_by(AccountDbo.account_id)
                        .with_for_update()
                    )
                ).all()
                accounts = tuple(dbo.as_domain() for dbo in dbos)
                assert all(isinstance(account, UserAccount) for account in accounts), (
                    "excluded SYSTEM rows in the WHERE clause"
                )
                return cast(tuple[UserAccount, ...], accounts)
        except Exception as exc:
            self._logger.exception("unexpected error locking accounts %r", account_ids)
            raise AccountRepositoryError(
                operation="get_many_for_update",
                cause=exc,
                metadata={"account_ids": [str(id_.value) for id_ in account_ids]},
            ) from exc

    @override
    async def find_by_owner(self, owner_id: OwnerId) -> tuple[UserAccount, ...]:
        try:
            async with self._session_factory() as session:
                dbos = (
                    await session.scalars(
                        select(AccountDbo)
                        .where(
                            AccountDbo.owner_id == owner_id.value,
                            # PLATFORM_OWNER_ID is never a real caller's owner_id, so this
                            # exclusion is defensive rather than load-bearing today -- excluded in
                            # the query itself anyway, matching get_for_update/get_many_for_update's
                            # own established pattern (T6, T7).
                            AccountDbo.account_type == AccountType.USER.value,
                        )
                        .order_by(AccountDbo.purpose, AccountDbo.currency)
                    )
                ).all()
                accounts = tuple(dbo.as_domain() for dbo in dbos)
                assert all(isinstance(account, UserAccount) for account in accounts), (
                    "excluded SYSTEM rows in the WHERE clause"
                )
                return cast(tuple[UserAccount, ...], accounts)
        except Exception as exc:
            self._logger.exception("unexpected error finding accounts for owner %s", owner_id)
            raise AccountRepositoryError(
                operation="find_by_owner",
                cause=exc,
                metadata={"owner_id": str(owner_id.value)},
            ) from exc

    @override
    async def update(self, account: UserAccount) -> None:
        try:
            async with self._session_factory() as session:
                await session.execute(
                    sa_update(AccountDbo)
                    .where(AccountDbo.account_id == account.account_id.value)
                    .values(
                        balance_amount=account.balance.amount,
                        status=account.status.value,
                        version=account.version,
                    )
                )
                await session.flush()
        except Exception as exc:
            self._logger.exception("unexpected error updating account %s", account.account_id)
            raise AccountRepositoryError(
                operation="update",
                cause=exc,
                metadata={"account_id": str(account.account_id.value)},
            ) from exc


def _violates_natural_key_constraint(exc: IntegrityError) -> bool:
    """Narrows the catch in `add()` to AO4's own constraint (migration
    `38ec8c622b3f`), not any `IntegrityError` -- a future constraint on this
    table must surface as itself, not get silently relabelled as a natural-
    key conflict the use case would then mishandle.
    """
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None) == _NATURAL_KEY_CONSTRAINT
