import hashlib
from collections.abc import Callable
from dataclasses import dataclass

from modules.account_balance.application.gateways.idempotency_repository import (
    IdempotencyRecord,
    IdempotencyRecordConflictError,
)
from modules.account_balance.application.gateways.transfer_repository import (
    TransferAlreadyReversedConflictError,
)
from modules.account_balance.application.gateways.unit_of_work import TransferUnitOfWork
from modules.account_balance.application.use_cases.transfer_money import (
    AccountNotFoundError,
    IdempotencyConflictError,
)
from modules.account_balance.domain import posting as domain_posting
from modules.account_balance.domain.account import Account
from modules.account_balance.domain.identifiers import EntryId, IdempotencyKey, OwnerId, TransferId
from modules.account_balance.domain.transfer import Transfer
from modules.shared.application.services.clock import Clock
from modules.shared.application.services.id_generator import IdGenerator

_IDEMPOTENCY_STATUS_COMPLETED = "COMPLETED"

# `AccountNotFoundError` and `IdempotencyConflictError` are reused verbatim
# from `transfer_money` (R3: "reuses transfer's idempotency mechanism
# exactly") rather than redeclared here -- two names for the same fact would
# force every caller catching either to know both exist.
__all__ = [
    "AccountNotFoundError",
    "IdempotencyConflictError",
    "RevertTransfer",
    "RevertTransferUseCase",
    "TransferAlreadyReversedError",
    "TransferNotFoundError",
]


class TransferNotFoundError(Exception):
    """`transfer_id` does not resolve to a persisted `Transfer` (R7).

    Not a `DomainError`: it is a fact about the request's own reference (the
    client named an id nothing backs), the same reasoning `AccountNotFoundError`
    already applies in `transfer_money.py`.
    """


class TransferAlreadyReversedError(Exception):
    """The original transfer already has a reversal (R4).

    Raised when the use case catches `TransferAlreadyReversedConflictError`
    -- the losing side of the partial-unique-index race on `transfers.reverses`
    (design §8). Distinct from `IdempotencyConflictError`: this is a conflict
    between *two different* idempotency keys reversing the same original, not
    the same key replayed with a different payload.
    """


@dataclass(frozen=True, slots=True)
class RevertTransfer:
    """`POST /transfers/{transfer_id}/reversals`'s use-case input (R2).

    Deliberately carries no source/destination -- both are derived from the
    original transfer, never supplied by the client (R2).
    """

    transfer_id: TransferId
    idempotency_key: IdempotencyKey
    requested_by: OwnerId


def _request_hash(request: RevertTransfer) -> str:
    """A stable hash over the one field a retry must not silently change:

    which transfer is being reversed (R2 -- there is no amount, source or
    destination in the request to include). Mirrors `transfer_money`'s own
    `_request_hash` shape (T3).
    """
    return hashlib.sha256(str(request.transfer_id).encode("utf-8")).hexdigest()


class RevertTransferUseCase:
    """Orchestrates a reversal end to end (R1-R7).

    Authentication (T2, resolving `X-Caller-Id`) is the inbound adapter's
    job, same as `TransferMoneyUseCase` -- this begins after the caller is
    already resolved. There is no authorization step at all (R1): the use
    case never calls `Account.assert_owned_by` against either leg.
    """

    def __init__(
        self,
        *,
        unit_of_work_factory: Callable[[], TransferUnitOfWork],
        id_generator: IdGenerator,
        clock: Clock,
    ) -> None:
        self._new_unit_of_work = unit_of_work_factory
        self._id_generator = id_generator
        self._clock = clock

    async def execute(self, request: RevertTransfer) -> Transfer:
        request_hash = _request_hash(request)

        try:
            async with self._new_unit_of_work() as uow:
                existing = await uow.idempotency.find_by_key(
                    caller_id=request.requested_by, idempotency_key=request.idempotency_key
                )
                if existing is not None:
                    return await self._replay_or_conflict(uow, existing, request_hash)
                return await self._post_new_reversal(uow, request, request_hash)
        except IdempotencyRecordConflictError:
            # T5, reused by R3: lost the race against a concurrent request
            # with the same key. The transaction above already rolled back
            # -- the winner's row is now visible to a fresh read.
            pass

        async with self._new_unit_of_work() as uow:
            existing = await uow.idempotency.find_by_key(
                caller_id=request.requested_by, idempotency_key=request.idempotency_key
            )
            if existing is None:  # pragma: no cover -- defensive, mirrors transfer_money
                raise RuntimeError(
                    "idempotency record vanished after losing the insert race for "
                    f"caller={request.requested_by}, key={request.idempotency_key}"
                )
            return await self._replay_or_conflict(uow, existing, request_hash)

    async def _replay_or_conflict(
        self, uow: TransferUnitOfWork, existing: IdempotencyRecord, request_hash: str
    ) -> Transfer:
        """T3, T4, reused by R3: same key + same payload replays; same key +

        a different payload is a conflict -- never a silent replacement.
        """
        if existing.request_hash != request_hash:
            raise IdempotencyConflictError(
                f"idempotency key {existing.idempotency_key} was already used with a "
                "different request"
            )
        transfer = await uow.transfers.get(existing.transfer_id)
        if transfer is None:  # pragma: no cover -- defensive: the record names a real transfer
            raise RuntimeError(
                f"idempotency record references transfer {existing.transfer_id}, not found"
            )
        return transfer

    async def _post_new_reversal(
        self, uow: TransferUnitOfWork, request: RevertTransfer, request_hash: str
    ) -> Transfer:
        reversal_id = TransferId(self._id_generator.next_id())
        occurred_at = self._clock.now()

        # R3: reserve the idempotency slot *before* loading the original
        # transfer or touching any account -- the same discipline
        # transfer_money's own T5 fix established (commit 0c4ea93):
        # nothing may run before the one real collision point (the unique
        # constraint on (caller_id, idempotency_key)) has a chance to
        # happen. `debit_for_reversal` cannot itself raise
        # `InsufficientFundsError` (R5), so there is no domain check on this
        # path a losing retry could fail on the way transfer's could -- the
        # ordering is kept anyway so this use case never has to be
        # re-audited for the same bug class the moment a future change adds
        # a check between the load and the write.
        await uow.idempotency.add(
            IdempotencyRecord(
                caller_id=request.requested_by,
                idempotency_key=request.idempotency_key,
                request_hash=request_hash,
                transfer_id=reversal_id,
                status=_IDEMPOTENCY_STATUS_COMPLETED,
                created_at=occurred_at,
            )
        )

        original = await uow.transfers.get(request.transfer_id)
        if original is None:
            raise TransferNotFoundError(f"transfer {request.transfer_id} does not exist")

        # R2: source/destination are derived from the original, never
        # supplied by the client. `revert()`'s own contract (posting.py):
        # the reversal debits the original's destination and credits the
        # original's source.
        source = await uow.accounts.get(original.destination_account_id)
        destination = await uow.accounts.get(original.source_account_id)
        if source is None or destination is None:  # pragma: no cover -- defensive:
            # both ids are FK-backed by the original transfer's own rows,
            # so this cannot happen through the API -- see AccountNotFoundError
            raise AccountNotFoundError(
                f"account referenced by transfer {request.transfer_id} does not exist"
            )

        source, destination = await self._lock_user_accounts(uow, source, destination)

        posting = domain_posting.revert(
            original,
            transfer_id=reversal_id,
            source=source,
            destination=destination,
            requested_by=request.requested_by,
            idempotency_key=request.idempotency_key,
            occurred_at=occurred_at,
            entry_ids=lambda: EntryId(self._id_generator.next_id()),
        )

        try:
            await uow.transfers.add(posting)
        except TransferAlreadyReversedConflictError as exc:
            raise TransferAlreadyReversedError(
                f"transfer {request.transfer_id} already has a reversal"
            ) from exc
        for account in posting.accounts:
            if account.account_type.is_user():
                await uow.accounts.update(account)

        return posting.transfer

    async def _lock_user_accounts(
        self, uow: TransferUnitOfWork, source: Account, destination: Account
    ) -> tuple[Account, Account]:
        """R6: exactly T6, unchanged -- locks the `USER`-typed leg(s) among

        the reversal's (derived) source/destination, sorted by `AccountId`,
        in that order. A `SYSTEM` leg is never locked and keeps the
        unlocked snapshot already loaded above -- its balance is never
        persisted (T7), so re-reading it under lock would buy nothing.
        """
        user_ids = sorted(
            {
                account.account_id
                for account in (source, destination)
                if account.account_type.is_user()
            }
        )
        for account_id in user_ids:
            locked = await uow.accounts.get_for_update(account_id)
            if locked is None:  # pragma: no cover -- defensive: already read unlocked above
                raise AccountNotFoundError(f"account {account_id} does not exist")
            if locked.account_id == source.account_id:
                source = locked
            if locked.account_id == destination.account_id:
                destination = locked
        return source, destination
