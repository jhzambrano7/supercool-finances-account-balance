from abc import ABC, abstractmethod
from typing import Any

from modules.account_balance.application.gateways.models.movement import MovementPage
from modules.account_balance.domain.identifiers import AccountId
from modules.shared.application.errors import ApplicationError, IntegrationError


class InvalidMovementCursorError(ApplicationError):
    """The `cursor` query parameter could not be decoded (docs/web-ui-plan.md §6.2: opaque to
    every caller but this port's own adapter -- once decoding fails, the caller sent a value this
    service never issued, an ordinary client mistake rather than a system fault).
    """

    def __init__(self, cursor: str) -> None:
        self.cursor = cursor
        super().__init__(f"cursor {cursor!r} could not be decoded")


class MovementRepositoryError(IntegrationError):
    """An unrecognized failure crossed this port's boundary -- the sibling of
    `AccountRepositoryError`/`TransferRepositoryError`, same shape and reason.
    """

    def __init__(self, operation: str, cause: Exception, metadata: dict[str, Any]) -> None:
        super().__init__(
            code=f"MOVEMENT_REPOSITORY_ERROR.{operation}",
            cause=cause,
            message=f"An error occurred while performing the {operation} operation",
            metadata=metadata,
        )


class MovementRepository(ABC):
    """Port for reading one account's ledger movements (docs/web-ui-plan.md §6.2).

    Deliberately its own port, not a new method on `TransferRepository`: a movement is a
    per-account read projection joining an `Entry` leg with its owning `Transfer`, not a
    `Transfer` itself -- and every existing `TransferRepository` implementor in this codebase
    (three fakes across `test_transfer_money.py`, `test_deposit.py`, `test_withdraw.py`) would
    otherwise need a method it has no reason to care about, just to keep implementing that port.
    """

    @abstractmethod
    async def find_by_account(
        self, *, account_id: AccountId, limit: int, cursor: str | None
    ) -> MovementPage:
        """Returns one page of `account_id`'s movements, newest first.

        `cursor` is opaque to every caller but this port's own adapter -- decoding it is the
        adapter's business (raising `InvalidMovementCursorError` if it can't), not a fact the
        application layer or its own caller needs to know.
        """
