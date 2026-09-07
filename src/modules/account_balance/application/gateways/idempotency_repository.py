from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from modules.account_balance.domain.identifiers import IdempotencyKey, OwnerId, TransferId


class IdempotencyRecordConflictError(Exception):
    """Raised by an adapter when `add()` loses the idempotency race (T5).

    Mirrors `AccountNaturalKeyConflictError` (AO4): deliberately not a
    `DomainError` -- whether `(caller_id, idempotency_key)` is already taken
    is a fact about *other rows*, the same kind of question the
    account-balance domain spec says no single aggregate can answer. This is
    a persistence-adapter concern, raised by whichever `IdempotencyRepository`
    implementation backs a real database.
    """


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    """The durable row PRD §6.1 describes -- a persistence row, not a domain
    type (it carries no invariant of its own; `TransferMoneyUseCase` is what
    gives its fields meaning).

    Deliberately does not carry a response body (PRD §6.1): a replay
    reconstructs its result from `TransferRepository.get(transfer_id)`
    instead, the same read path any other query would use.
    """

    caller_id: OwnerId
    idempotency_key: IdempotencyKey
    request_hash: str
    transfer_id: TransferId
    status: str
    created_at: datetime


class IdempotencyRepository(ABC):
    """Port for the durable idempotency row (T3, T9's second sibling port)."""

    @abstractmethod
    async def find_by_key(
        self, *, caller_id: OwnerId, idempotency_key: IdempotencyKey
    ) -> IdempotencyRecord | None:
        """Returns the record for this caller and key, or `None` if none exists yet."""

    @abstractmethod
    async def add(self, record: IdempotencyRecord) -> None:
        """Persists a new idempotency record.

        Raises `IdempotencyRecordConflictError` if `(caller_id,
        idempotency_key)` already exists -- the losing side of the T5 race.
        Does not commit -- see `TransferRepository.add`.
        """
