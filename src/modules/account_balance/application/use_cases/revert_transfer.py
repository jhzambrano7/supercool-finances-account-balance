import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from logging import Logger

from modules.account_balance.application.gateways.account_repository import AccountNotFoundError
from modules.account_balance.application.gateways.authorization_gateway import (
    AuthorizationGateway,
)
from modules.account_balance.application.gateways.idempotency_repository import (
    IdempotencyRecord,
    IdempotencyRecordConflictError,
)
from modules.account_balance.application.gateways.models.find_accounts_criteria import (
    FindAccountByAccountId,
)
from modules.account_balance.application.gateways.transfer_repository import (
    TransferAlreadyReversedConflictError,
)
from modules.account_balance.application.gateways.unit_of_work import TransferUnitOfWork
from modules.account_balance.application.use_cases.transfer_money import IdempotencyConflictError
from modules.account_balance.domain import posting as domain_posting
from modules.account_balance.domain.account import Account, UserAccount
from modules.account_balance.domain.identifiers import (
    EntryId,
    IdempotencyKey,
    PrincipalId,
    TransferId,
)
from modules.account_balance.domain.transfer import Transfer
from modules.shared.application.errors import ApplicationError, ResourceNotFoundError
from modules.shared.application.services.clock import Clock
from modules.shared.application.services.id_generator import IdGenerator


class TransferNotFoundError(ResourceNotFoundError):
    """`transfer_id` does not resolve to a persisted `Transfer` (R7).

    Not a `DomainError`: it is a fact about the request's own reference (the
    client named an id nothing backs), the same reasoning `AccountNotFoundError`
    already applies in `transfer_money.py`.
    """

    def __init__(self, transfer_id: TransferId) -> None:
        super().__init__(resource_type="transfer", resource_identifier=str(transfer_id))


class TransferAlreadyReversedError(ApplicationError):
    """The original transfer already has a reversal (R4).

    Raised when the use case catches `TransferAlreadyReversedConflictError`
    -- the losing side of the partial-unique-index race on `transfers.reverses`
    (design §8). Distinct from `IdempotencyConflictError`: this is a conflict
    between *two different* idempotency keys reversing the same original, not
    the same key replayed with a different payload.
    """

    def __init__(self, *, original_transfer_id: TransferId) -> None:
        self.original_transfer_id = original_transfer_id
        super().__init__(f"transfer {original_transfer_id} already has a reversal")


@dataclass(frozen=True, slots=True)
class RevertTransferRequest:
    """`POST /transfers/{transfer_id}/reversals`'s use-case input (R2).

    Deliberately carries no source/destination -- both are derived from the
    original transfer, never supplied by the client (R2).
    """

    transfer_id: TransferId
    idempotency_key: IdempotencyKey
    executed_by: PrincipalId

    def hash(self) -> str:
        """A stable hash over the one field a retry must not silently change:

        which transfer is being reversed (R2 -- there is no amount, source or
        destination in the request to include). Mirrors `TransferMoneyRequest.hash()`'s own
        shape (T3): a canonical, delimiter-free encoding rather than the dataclass's own
        `repr`, so a field reorder or rename does not change what a retry means.
        """
        return hashlib.sha256(str(self.transfer_id).encode("utf-8")).hexdigest()


class RevertTransfer:
    """Orchestrates a reversal end to end (R1-R7).

    Authentication (T2, resolving `X-Caller-Id`) is the inbound adapter's job, same as
    `TransferMoney` -- this begins after the caller is already resolved. Authorization (R1) is a
    real, if simulated, check: `execute()` asks `AuthorizationGateway` whether `executed_by` is the
    platform's one authorized admin principal before doing anything else. This is deliberately not
    `Account.assert_owned_by` against either leg -- an operator reversing a transfer has no
    ownership relationship to either account to assert in the first place (R1's own point: an
    operator may reverse any transfer, not just ones touching accounts it owns).
    """

    def __init__(
        self,
        *,
        logger: Logger,
        unit_of_work_factory: Callable[[], TransferUnitOfWork],
        id_generator: IdGenerator,
        clock: Clock,
        authorization_gateway: AuthorizationGateway,
    ) -> None:
        self._logger = logger
        self._new_unit_of_work = unit_of_work_factory
        self._id_generator = id_generator
        self._clock = clock
        self._authorization_gateway = authorization_gateway

    async def execute(self, request: RevertTransferRequest) -> Transfer:
        # Checked before anything transactional: a stateless gate over who is
        # calling, not a fact this or any race could invalidate, so R3's "nothing
        # may run before the one real collision point" ordering rule does not
        # apply to it -- there is no collision here to protect.
        await self._authorization_gateway.authorize(request.executed_by)
        request_hash = request.hash()

        try:
            async with self._new_unit_of_work() as uow:
                existing = await uow.idempotency.find_by_key(
                    caller_id=request.executed_by, idempotency_key=request.idempotency_key
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
                caller_id=request.executed_by, idempotency_key=request.idempotency_key
            )
            if existing is None:  # pragma: no cover -- defensive, mirrors transfer_money
                raise RuntimeError(
                    "idempotency record vanished after losing the insert race for "
                    f"caller={request.executed_by}, key={request.idempotency_key}"
                )
            return await self._replay_or_conflict(uow, existing, request_hash)

    async def _replay_or_conflict(
        self, uow: TransferUnitOfWork, existing: IdempotencyRecord, request_hash: str
    ) -> Transfer:
        """T3, T4, reused by R3: same key + same payload replays; same key +

        a different payload is a conflict -- never a silent replacement.
        """
        if existing.request_hash != request_hash:
            self._logger.warning(
                "idempotency key %s reused with a different payload by caller %s",
                existing.idempotency_key,
                existing.caller_id,
            )
            raise IdempotencyConflictError(
                caller_id=existing.caller_id, idempotency_key=existing.idempotency_key
            )
        transfer = await uow.transfers.get(existing.transfer_id)
        if transfer is None:  # pragma: no cover -- defensive: the record names a real transfer
            self._logger.error(
                "idempotency record references transfer %s, which does not exist",
                existing.transfer_id,
            )
            raise RuntimeError(
                f"idempotency record references transfer {existing.transfer_id}, not found"
            )
        return transfer

    async def _post_new_reversal(
        self, uow: TransferUnitOfWork, request: RevertTransferRequest, request_hash: str
    ) -> Transfer:
        reversal_id = TransferId(self._id_generator.next_id())
        occurred_at = self._clock.now()

        # R3: reserve the idempotency slot *before* loading the original
        # transfer or touching any account -- the same discipline
        # transfer_money's own T5 fix established: nothing may run before
        # the one real collision point (the unique constraint on
        # (caller_id, idempotency_key)) has a chance to happen.
        # `debit_for_reversal` cannot itself raise `InsufficientFundsError`
        # (R5), so there is no domain check on this path a losing retry
        # could fail on the way transfer's could -- the ordering is kept
        # anyway so this use case never has to be re-audited for the same
        # bug class the moment a future change adds a check between the
        # load and the write.
        await uow.idempotency.add(
            IdempotencyRecord(
                caller_id=request.executed_by,
                idempotency_key=request.idempotency_key,
                request_hash=request_hash,
                transfer_id=reversal_id,
                created_at=occurred_at,
            )
        )

        original = await uow.transfers.get(request.transfer_id)
        if original is None:
            self._logger.warning("transfer %s does not exist, cannot revert", request.transfer_id)
            raise TransferNotFoundError(request.transfer_id)

        # R2: source/destination are derived from the original, never
        # supplied by the client. `revert()`'s own contract (posting.py):
        # the reversal debits the original's destination and credits the
        # original's source.
        source = await uow.accounts.get(
            criteria=FindAccountByAccountId(original.destination_account_id)
        )
        destination = await uow.accounts.get(
            criteria=FindAccountByAccountId(original.source_account_id)
        )

        source, destination = await self._lock_user_accounts(uow, source, destination)

        posting = domain_posting.revert(
            original,
            transfer_id=reversal_id,
            source=source,
            destination=destination,
            requested_by=request.executed_by,
            idempotency_key=request.idempotency_key,
            occurred_at=occurred_at,
            entry_ids=lambda: EntryId(self._id_generator.next_id()),
        )

        try:
            await uow.transfers.add(posting.transfer)
        except TransferAlreadyReversedConflictError as exc:
            self._logger.warning(
                "transfer %s already has a reversal, rejecting a second one",
                request.transfer_id,
            )
            raise TransferAlreadyReversedError(original_transfer_id=request.transfer_id) from exc
        # `posting.accounts` holds only `UserAccount`s (T7) -- a `SYSTEM`
        # leg has no balance to persist, so there is nothing left to filter
        # here, same as `TransferMoney._post_new_transfer`.
        for account in posting.accounts:
            await uow.accounts.update(account)

        return posting.transfer

    async def _lock_user_accounts(
        self, uow: TransferUnitOfWork, source: Account, destination: Account
    ) -> tuple[Account, Account]:
        """R6: exactly T6, unchanged -- locks the `USER`-typed leg(s) among the reversal's
        (derived) source/destination. The lock *ordering* that keeps a concurrent reversal and
        counter-reversal from deadlocking is the adapter's job, not this method's --
        `get_many_for_update` sorts what it is given. A `SYSTEM` leg is never locked and keeps
        the unlocked snapshot already loaded above: it has no balance to persist (T7), so
        re-reading it under lock would buy nothing."""
        account_ids = tuple(
            {
                account.account_id
                for account in (source, destination)
                if isinstance(account, UserAccount)
            }
        )
        locked_accounts = await uow.accounts.get_many_for_update(account_ids)
        locked_by_id = {account.account_id: account for account in locked_accounts}

        for account_id in account_ids:
            if account_id not in locked_by_id:  # pragma: no cover -- defensive: read unlocked above
                self._logger.error(
                    "account %s vanished between the unlocked read and the lock", account_id
                )
                raise AccountNotFoundError(FindAccountByAccountId(account_id))

        source = locked_by_id.get(source.account_id, source)
        destination = locked_by_id.get(destination.account_id, destination)
        return source, destination
