from abc import ABC, abstractmethod

from modules.account_balance.domain.identifiers import TransferId
from modules.account_balance.domain.posting import Posting
from modules.account_balance.domain.transfer import Transfer


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
        """

    @abstractmethod
    async def get(self, transfer_id: TransferId) -> Transfer | None:
        """Reconstructs a `Transfer`, with its entries, from the ledger.

        This is the read path an idempotent replay uses (T3, T4): the
        response is rebuilt from here, never from a stored response body.
        """
