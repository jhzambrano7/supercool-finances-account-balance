from abc import ABC, abstractmethod
from typing import Any

from modules.account_balance.application.gateways.models.negative_balance import (
    NegativeBalanceAccount,
)
from modules.shared.application.errors import IntegrationError


class CollectionsRepositoryError(IntegrationError):
    """An unrecognized failure crossed this port's boundary -- the sibling of
    `AccountRepositoryError`/`MovementRepositoryError`, same shape and reason.
    """

    def __init__(self, operation: str, cause: Exception, metadata: dict[str, Any]) -> None:
        super().__init__(
            code=f"COLLECTIONS_REPOSITORY_ERROR.{operation}",
            cause=cause,
            message=f"An error occurred while performing the {operation} operation",
            metadata=metadata,
        )


class CollectionsRepository(ABC):
    """Port for reading every `USER` account currently in negative balance (PRD §11.3).

    Deliberately its own port, not a new method on `AccountRepository`: answering "how long has
    this account been negative" requires walking `entries`, not just reading the materialized
    `accounts.balance_amount` column -- the same reasoning `MovementRepository` already gives for
    being a distinct port rather than a new `TransferRepository` method. Every existing
    `AccountRepository` fake across the test suite would otherwise need a method it has no reason
    to care about, just to keep implementing that port.
    """

    @abstractmethod
    async def find_negative_balances(self) -> tuple[NegativeBalanceAccount, ...]:
        """Returns every `USER` account whose materialized balance is currently negative, each
        paired with the timestamp its *current* negative episode began.

        "Current episode" matters: an account that went negative, was topped up back to `>= 0`,
        and later went negative again must report the second episode's start, not the first --
        see `queries/negative_balances.py` for how that is computed (`sql_collections_repository.py`
        only executes the statement it builds). `SYSTEM` accounts never appear here (PRD §5.3):
        they never materialize a balance in the first place, so "negative" is not even a question
        that can be asked of one.

        `negative_since` on a returned row is `None` when the account is negative right now but no
        entry history explains it -- a materialized-balance drift (PRD §11.1), not the ordinary
        case. Such an account is still returned, never dropped: see
        `NegativeBalanceAccount`'s own docstring for why.

        No pagination in v1: the demo's account population is small enough that a full scan is
        cheap, and PRD §11.3 asks for this as a single operator-facing signal, not a paged list.
        A ledger with a large negative-balance population would need this to page the way
        `MovementRepository.find_by_account` already does -- deferred here for the same reason
        `find_by_owner` defers it: no evidence yet that it is needed.
        """
