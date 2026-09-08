import hashlib
from collections.abc import Callable
from dataclasses import dataclass

from modules.account_balance.application.gateways.account_repository import AccountNotFoundError
from modules.account_balance.application.gateways.idempotency_repository import (
    IdempotencyRecord,
    IdempotencyRecordConflictError,
)
from modules.account_balance.application.gateways.models.find_accounts_criteria import (
    FindAccountByAccountId,
)
from modules.account_balance.application.gateways.unit_of_work import TransferUnitOfWork
from modules.account_balance.domain import posting as domain_posting
from modules.account_balance.domain.account import Account, UserAccount
from modules.account_balance.domain.identifiers import (
    AccountId,
    EntryId,
    IdempotencyKey,
    OwnerId,
    TransferId,
)
from modules.account_balance.domain.transfer import Transfer
from modules.shared.application.errors import ApplicationError
from modules.shared.application.services.clock import Clock
from modules.shared.application.services.id_generator import IdGenerator
from modules.shared.domain.money import Money

_IDEMPOTENCY_STATUS_COMPLETED = "COMPLETED"

# Re-exported so a route can catch this without importing the port module
# directly -- the same `AccountNotFoundError` `account-opening`'s own
# AccountRepository.get() raises, not a second, transfer-local type for the
# same fact (an id the client sent resolves to nothing).
__all__ = [
    "AccountNotFoundError",
    "IdempotencyConflictError",
    "SystemToSystemTransferNotAllowedError",
    "TransferMoney",
    "TransferMoneyRequest",
]


class IdempotencyConflictError(ApplicationError):
    """The idempotency key was already used with a *different* request (T3).

    Distinct from `IdempotencyRecordConflictError`, the adapter-level
    unique-violation T5's same-payload race resolves by replay: this one is
    a genuine client error -- the two attempts disagree about what actually
    happened, so it is rejected rather than silently resolved.
    """

    def __init__(self, *, caller_id: OwnerId, idempotency_key: IdempotencyKey) -> None:
        self.caller_id = caller_id
        self.idempotency_key = idempotency_key
        super().__init__(
            f"idempotency key {idempotency_key} was already used with a different request"
        )


class SystemToSystemTransferNotAllowedError(ApplicationError):
    """Neither leg is a `USER` account.

    PRD §9.1's fourth movement shape ("system movement") requires operator
    authority, which this HTTP-facing, customer-caller endpoint has no
    mechanism to grant (T1) -- this is what refuses it rather than silently
    picking a leg to check ownership over.
    """

    def __init__(self, *, source_account_id: AccountId, destination_account_id: AccountId) -> None:
        self.source_account_id = source_account_id
        self.destination_account_id = destination_account_id
        super().__init__(
            f"neither {source_account_id} nor {destination_account_id} is a USER account"
        )


@dataclass(frozen=True, slots=True)
class TransferMoneyRequest:
    """Use-case input (design §5.3) -- the client-facing shape.

    Covers transfer, deposit and withdraw alike (there is no separate concept
    for any of the three -- spec Purpose).
    """

    source_account_id: AccountId
    destination_account_id: AccountId
    amount: Money
    idempotency_key: IdempotencyKey
    requested_by: OwnerId

    def hash(self) -> str:
        """A stable hash over exactly the fields a retry must not silently change (T3).

        Source, destination, amount and currency. Deliberately a canonical,
        delimiter-joined encoding rather than the dataclass's own `repr` -- a
        field reorder or rename must not change what a retry means.
        """
        canonical = "|".join(
            (
                str(self.source_account_id),
                str(self.destination_account_id),
                str(self.amount.amount),
                str(self.amount.currency),
            )
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class TransferMoney:
    """Orchestrates a transfer end to end (T1-T7).

    Authentication (T2, resolving `X-Caller-Id`) is the inbound adapter's
    job -- this begins at authorization, over an already-resolved caller.
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

    async def execute(self, request: TransferMoneyRequest) -> Transfer:
        request_hash = request.hash()

        try:
            async with self._new_unit_of_work() as uow:
                existing = await uow.idempotency.find_by_key(
                    caller_id=request.requested_by, idempotency_key=request.idempotency_key
                )
                if existing is not None:
                    return await self._replay_or_conflict(uow, existing, request_hash)
                return await self._post_new_transfer(uow, request, request_hash)
        except IdempotencyRecordConflictError:
            # T5: lost the race against a concurrent request with the same
            # key. The transaction above already rolled back (the unit of
            # work's __aexit__ saw this exception propagate) -- the winner's
            # row is now visible to a fresh read, outside that failed
            # transaction.
            pass

        async with self._new_unit_of_work() as uow:
            existing = await uow.idempotency.find_by_key(
                caller_id=request.requested_by, idempotency_key=request.idempotency_key
            )
            if existing is None:  # pragma: no cover -- defensive, mirrors AO4
                raise RuntimeError(
                    "idempotency record vanished after losing the insert race for "
                    f"caller={request.requested_by}, key={request.idempotency_key}"
                )
            return await self._replay_or_conflict(uow, existing, request_hash)

    async def _replay_or_conflict(
        self, uow: TransferUnitOfWork, existing: IdempotencyRecord, request_hash: str
    ) -> Transfer:
        """T3, T4: same key + same payload replays; same key + a different payload is a conflict.

        Never a silent replacement.
        """
        if existing.request_hash != request_hash:
            raise IdempotencyConflictError(
                caller_id=existing.caller_id, idempotency_key=existing.idempotency_key
            )
        transfer = await uow.transfers.get(existing.transfer_id)
        if transfer is None:  # pragma: no cover -- defensive: the record names a real transfer
            raise RuntimeError(
                f"idempotency record references transfer {existing.transfer_id}, not found"
            )
        return transfer

    async def _post_new_transfer(
        self, uow: TransferUnitOfWork, request: TransferMoneyRequest, request_hash: str
    ) -> Transfer:
        transfer_id = TransferId(self._id_generator.next_id())
        occurred_at = self._clock.now()

        # T5: reserve the idempotency slot *before* touching any account --
        # found by an independent review that reordering this to the end (as
        # a first version of this use case did) breaks the race exactly when
        # the retry would fail on its own merits (e.g. the winner's debit
        # already exhausted the balance): the loser would reach that failure
        # before ever attempting the insert this recovery depends on. The
        # unique constraint on (caller_id, idempotency_key) is the only place
        # two concurrent requests sharing a key can actually collide, so nothing
        # else may run before this collision has a chance to happen. May raise
        # IdempotencyRecordConflictError (T5) -- caught by execute(). The FK to
        # `transfers.transfer_id` is deferred to commit for exactly this
        # reason: this row is inserted before that one exists.
        await uow.idempotency.add(
            IdempotencyRecord(
                caller_id=request.requested_by,
                idempotency_key=request.idempotency_key,
                request_hash=request_hash,
                transfer_id=transfer_id,
                status=_IDEMPOTENCY_STATUS_COMPLETED,
                created_at=occurred_at,
            )
        )

        # get(), not find(): AccountRepository's own find-or-raise already
        # names the reachable fact ("this id resolves to nothing") -- a
        # manual None-check re-deriving the same error would just be this
        # port's own AccountNotFoundError, reimplemented at the call site.
        source = await uow.accounts.get(criteria=FindAccountByAccountId(request.source_account_id))
        destination = await uow.accounts.get(
            criteria=FindAccountByAccountId(request.destination_account_id)
        )

        self._assert_authorized(source=source, destination=destination, caller=request.requested_by)

        source, destination = await self._lock_user_accounts(uow, source, destination)

        posting = domain_posting.transfer(
            transfer_id=transfer_id,
            source=source,
            destination=destination,
            amount=request.amount,
            requested_by=request.requested_by,
            idempotency_key=request.idempotency_key,
            occurred_at=occurred_at,
            entry_ids=lambda: EntryId(self._id_generator.next_id()),
        )

        await uow.transfers.add(posting)
        for account in posting.accounts:
            if isinstance(account, UserAccount):
                await uow.accounts.update(account)

        return posting.transfer

    async def _lock_user_accounts(
        self, uow: TransferUnitOfWork, source: Account, destination: Account
    ) -> tuple[Account, Account]:
        """T6: locks the `USER`-typed leg(s), sorted by `AccountId`, in that order.

        This is the deterministic ordering that keeps a concurrent A->B and
        B->A transfer from deadlocking. A `SYSTEM` leg is never locked and
        keeps the unlocked snapshot already loaded above -- its balance is
        never persisted (T7), so re-reading it under lock would buy nothing.
        """
        user_ids = sorted(
            {
                account.account_id
                for account in (source, destination)
                if isinstance(account, UserAccount)
            }
        )
        for account_id in user_ids:
            locked = await uow.accounts.get_for_update(account_id)
            if locked is None:  # pragma: no cover -- defensive: already read unlocked above
                raise AccountNotFoundError(FindAccountByAccountId(account_id))
            if locked.account_id == source.account_id:
                source = locked
            if locked.account_id == destination.account_id:
                destination = locked
        return source, destination

    def _assert_authorized(self, *, source: Account, destination: Account, caller: OwnerId) -> None:
        """T1, PRD §9.1's table: authorization is stated over the debited leg(s).

        Computed from the two accounts' *types* -- not a fixed "caller owns
        the source" assumption, which would wrongly block every deposit.
        """
        if isinstance(source, UserAccount):
            source.assert_owned_by(caller)
        elif isinstance(destination, UserAccount):
            destination.assert_owned_by(caller)
        else:
            raise SystemToSystemTransferNotAllowedError(
                source_account_id=source.account_id, destination_account_id=destination.account_id
            )
