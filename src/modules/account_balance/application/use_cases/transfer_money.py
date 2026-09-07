import hashlib
from collections.abc import Callable
from dataclasses import dataclass

from modules.account_balance.application.gateways.idempotency_repository import (
    IdempotencyRecord,
    IdempotencyRecordConflictError,
)
from modules.account_balance.application.gateways.unit_of_work import TransferUnitOfWork
from modules.account_balance.domain import posting as domain_posting
from modules.account_balance.domain.account import Account
from modules.account_balance.domain.identifiers import (
    AccountId,
    EntryId,
    IdempotencyKey,
    OwnerId,
    TransferId,
)
from modules.account_balance.domain.transfer import Transfer
from modules.shared.application.services.clock import Clock
from modules.shared.application.services.id_generator import IdGenerator
from modules.shared.domain.money import Money

_IDEMPOTENCY_STATUS_COMPLETED = "COMPLETED"


class AccountNotFoundError(Exception):
    """Either leg's account id does not resolve to a persisted account.

    Not a `DomainError`: it is a fact about the request's own references
    (the client sent an id nothing backs), not an invariant `Account` or
    `Transfer` enforce over aggregates already loaded from real rows.
    """


class IdempotencyConflictError(Exception):
    """The idempotency key was already used with a *different* request (T3).

    Distinct from `IdempotencyRecordConflictError`, the adapter-level
    unique-violation T5's same-payload race resolves by replay: this one is
    a genuine client error -- the two attempts disagree about what actually
    happened, so it is rejected rather than silently resolved.
    """


class SystemToSystemTransferNotAllowedError(Exception):
    """Neither leg is a `USER` account.

    PRD §9.1's fourth movement shape ("system movement") requires operator
    authority, which this HTTP-facing, customer-caller endpoint has no
    mechanism to grant (T1) -- this is what refuses it rather than silently
    picking a leg to check ownership over.
    """


@dataclass(frozen=True, slots=True)
class TransferMoney:
    """`POST /transfers`'s use-case input (design §5.3) -- the client-facing

    shape covering transfer, deposit and withdraw alike (there is no
    separate concept for any of the three -- spec Purpose).
    """

    source_account_id: AccountId
    destination_account_id: AccountId
    amount: Money
    idempotency_key: IdempotencyKey
    requested_by: OwnerId


def _request_hash(request: TransferMoney) -> str:
    """A stable hash over exactly the fields a retry must not silently

    change (T3): source, destination, amount and currency. Deliberately a
    canonical, delimiter-joined encoding rather than the dataclass's own
    `repr` -- a field reorder or rename must not change what a retry means.
    """
    canonical = "|".join(
        (
            str(request.source_account_id),
            str(request.destination_account_id),
            str(request.amount.amount),
            str(request.amount.currency),
        )
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class TransferMoneyUseCase:
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

    async def execute(self, request: TransferMoney) -> Transfer:
        request_hash = _request_hash(request)

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
        """T3, T4: same key + same payload replays; same key + a different

        payload is a conflict -- never a silent replacement.
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

    async def _post_new_transfer(
        self, uow: TransferUnitOfWork, request: TransferMoney, request_hash: str
    ) -> Transfer:
        source = await uow.accounts.get(request.source_account_id)
        destination = await uow.accounts.get(request.destination_account_id)
        if source is None:
            raise AccountNotFoundError(f"account {request.source_account_id} does not exist")
        if destination is None:
            raise AccountNotFoundError(f"account {request.destination_account_id} does not exist")

        self._assert_authorized(source=source, destination=destination, caller=request.requested_by)

        source, destination = await self._lock_user_accounts(uow, source, destination)

        transfer_id = TransferId(self._id_generator.next_id())
        posting = domain_posting.transfer(
            transfer_id=transfer_id,
            source=source,
            destination=destination,
            amount=request.amount,
            requested_by=request.requested_by,
            idempotency_key=request.idempotency_key,
            occurred_at=self._clock.now(),
            entry_ids=lambda: EntryId(self._id_generator.next_id()),
        )

        await uow.transfers.add(posting)
        for account in posting.accounts:
            if account.account_type.is_user():
                await uow.accounts.update(account)
        # May raise IdempotencyRecordConflictError (T5) -- caught by execute().
        await uow.idempotency.add(
            IdempotencyRecord(
                caller_id=request.requested_by,
                idempotency_key=request.idempotency_key,
                request_hash=request_hash,
                transfer_id=transfer_id,
                status=_IDEMPOTENCY_STATUS_COMPLETED,
                created_at=posting.transfer.occurred_at,
            )
        )

        return posting.transfer

    async def _lock_user_accounts(
        self, uow: TransferUnitOfWork, source: Account, destination: Account
    ) -> tuple[Account, Account]:
        """T6: locks the `USER`-typed leg(s), sorted by `AccountId`, in that

        order -- the deterministic ordering that keeps a concurrent A->B and
        B->A transfer from deadlocking. A `SYSTEM` leg is never locked and
        keeps the unlocked snapshot already loaded above -- its balance is
        never persisted (T7), so re-reading it under lock would buy nothing.
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

    def _assert_authorized(self, *, source: Account, destination: Account, caller: OwnerId) -> None:
        """T1, PRD §9.1's table: authorization is stated over the debited

        leg(s), computed from the two accounts' *types* -- not a fixed
        "caller owns the source" assumption, which would wrongly block
        every deposit.
        """
        if source.account_type.is_user():
            source.assert_owned_by(caller)
        elif destination.account_type.is_user():
            destination.assert_owned_by(caller)
        else:
            raise SystemToSystemTransferNotAllowedError(
                f"neither {source.account_id} nor {destination.account_id} is a USER account"
            )
