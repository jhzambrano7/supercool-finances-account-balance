from abc import ABC, abstractmethod

from modules.account_balance.domain.identifiers import TransferId
from modules.account_balance.domain.posting import Posting
from modules.account_balance.domain.transfer import Transfer


class TransferAlreadyReversedConflictError(Exception):
    """Raised by an adapter when `add()` loses the R4 race.

    Mirrors `AccountNaturalKeyConflictError` (AO4) and
    `IdempotencyRecordConflictError` (T5): whether some *other* transfer
    already reverses the same original is a fact about other rows, which no
    single `Posting` can answer for itself -- a persistence-adapter concern,
    raised by whichever `TransferRepository` implementation backs a real
    database when the partial unique index on `transfers.reverses` (design
    §8, R4) is violated. Never raised for an ordinary (non-reversal) posting,
    since that index only ever applies to rows with `reverses IS NOT NULL`.
    """


class TransferRepository(ABC):
    """Port for persisting and reconstructing a posted `Transfer` (T9's
    sibling port to `AccountRepository`): a transfer's own row and its
    entries, never a balance.
    """

    @abstractmethod
    async def add(self, posting: Posting) -> None:
        """Persists `posting.transfer` and every one of its entries.

        Does not commit -- the enclosing `TransferUnitOfWork` commits once,
        atomically, alongside the account balance updates and the
        idempotency record (T3, PRD §6.1).

        Raises `TransferAlreadyReversedConflictError` if `posting.transfer`
        is a reversal (`reverses` is not `None`) and some other transfer
        already reverses the same original -- the losing side of the R4
        race (design §8).
        """

    @abstractmethod
    async def get(self, transfer_id: TransferId) -> Transfer | None:
        """Reconstructs a `Transfer`, with its entries, from the ledger.

        This is the read path an idempotent replay uses (T3, T4): the
        response is rebuilt from here, never from a stored response body.
        """
