from collections.abc import Callable
from logging import Logger
from typing import override

from sqlalchemy.ext.asyncio import AsyncSession

from modules.account_balance.adapters.outbound.repositories.sql.queries.negative_balances import (
    negative_balances_query,
)
from modules.account_balance.application.gateways.collections_repository import (
    CollectionsRepository,
    CollectionsRepositoryError,
)
from modules.account_balance.application.gateways.models.negative_balance import (
    NegativeBalanceAccount,
)
from modules.account_balance.domain.identifiers import AccountId, OwnerId
from modules.shared.domain.money import Currency, Money


class SqlCollectionsRepository(CollectionsRepository):
    """SQLAlchemy async adapter for `CollectionsRepository` (PRD §11.3).

    A fresh, independently-committed session per call, matching `SqlMovementRepository`: this is a
    read, not part of any write transaction, so there is no unit of work to share.
    """

    def __init__(self, logger: Logger, session_factory: Callable[[], AsyncSession]) -> None:
        self._logger = logger
        self._session_factory = session_factory

    @override
    async def find_negative_balances(self) -> tuple[NegativeBalanceAccount, ...]:
        try:
            async with self._session_factory() as session:
                rows = (await session.execute(negative_balances_query())).all()
        except Exception as exc:
            self._logger.exception("unexpected error finding negative-balance accounts")
            raise CollectionsRepositoryError(
                operation="find_negative_balances", cause=exc, metadata={}
            ) from exc

        return tuple(
            NegativeBalanceAccount(
                account_id=AccountId(row.account_id),
                owner_id=OwnerId(row.owner_id),
                balance=Money(row.balance_amount, Currency(row.currency)),
                negative_since=row.negative_since,
            )
            for row in rows
        )
