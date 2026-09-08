from abc import ABC, abstractmethod
from typing import Any

from modules.account_balance.domain.identifiers import TransferId
from modules.account_balance.domain.transfer import Transfer
from modules.shared.application.errors import IntegrationError, ResourceAlreadyExistsError


class TransferRepositoryError(IntegrationError):
    """An unrecognized failure crossed this port's boundary (point 4 of the
    adapter conventions: no third-party exception leaks past a repository).

    The sibling of `AccountRepositoryError`, same shape and same reason.
    """

    def __init__(self, operation: str, cause: Exception, metadata: dict[str, Any]) -> None:
        super().__init__(
            code=f"TRANSFER_REPOSITORY_ERROR.{operation}",
            cause=cause,
            message=f"An error occurred while performing the {operation} operation",
            metadata=metadata,
        )


class TransferAlreadyReversedConflictError(ResourceAlreadyExistsError):
    """Raised by an adapter when `add()` loses the R4 race.

    Mirrors `AccountAlreadyExistsError` (AO4) and `IdempotencyRecordConflictError`
    (T5): whether some *other* transfer already reverses the same original is a
    fact about other rows, which no single `Transfer` can answer for itself --
    a persistence-adapter concern, raised by whichever `TransferRepository`
    implementation backs a real database when the partial unique index on
    `transfers.reverses` (design §8, R4) is violated. Never raised for an
    ordinary (non-reversal) transfer, since that index only ever applies to
    rows with `reverses IS NOT NULL`.
    """

    def __init__(self, *, original_transfer_id: TransferId) -> None:
        self.original_transfer_id = original_transfer_id
        super().__init__(
            resource_type="transfer_reversal",
            resource_identifier=str(original_transfer_id),
        )


class TransferRepository(ABC):
    """Port for persisting and reconstructing a posted `Transfer` (T9's
    sibling port to `AccountRepository`): a transfer's own row and its
    entries, never a balance.
    """

    @abstractmethod
    async def add(self, transfer: Transfer) -> None:
        """Persists the transfer and every one of its entries.

        Takes a `Transfer`, not a `Posting`: a `Transfer` already owns its
        `Entry` legs, which is the whole of what this repository writes. The
        accounts a `Posting` also carries belong to `AccountRepository` --
        the use case holds the `Posting` and hands each repository the part
        that is its own.

        Does not commit -- the enclosing `TransferUnitOfWork` commits once,
        atomically, alongside the account balance updates and the
        idempotency record (T3, PRD §6.1).

        Raises `TransferAlreadyReversedConflictError` if `transfer` is a
        reversal (`reverses` is not `None`) and some other transfer already
        reverses the same original -- the losing side of the R4 race
        (design §8).
        """

    @abstractmethod
    async def get(self, transfer_id: TransferId) -> Transfer | None:
        """Reconstructs a `Transfer`, with its entries, from the ledger.

        This is the read path an idempotent replay uses (T3, T4): the
        response is rebuilt from here, never from a stored response body.
        """
