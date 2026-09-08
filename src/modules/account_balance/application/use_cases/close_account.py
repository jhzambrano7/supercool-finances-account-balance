from collections.abc import Callable
from dataclasses import dataclass
from logging import Logger

from modules.account_balance.application.gateways.account_unit_of_work import AccountUnitOfWork
from modules.account_balance.application.gateways.models.find_accounts_criteria import (
    FindAccountByAccountId,
)
from modules.account_balance.domain.account import UserAccount
from modules.account_balance.domain.errors import AccountOwnershipError
from modules.account_balance.domain.identifiers import AccountId, OwnerId


@dataclass(frozen=True, slots=True)
class CloseAccountRequest:
    account_id: AccountId
    caller_id: OwnerId


class CloseAccount:
    """Closes a `USER` account (§7.2) -- `Account.close()` already does the two domain refusals
    (`AccountNotClosableError`, `AccountNotEmptyError`); this orchestrates the transaction and
    the lock around it, the same shape `TransferMoney`/`RevertTransfer` use for exactly the same
    reason: the balance check and the write must happen under one held lock, or a concurrent
    deposit could land in the gap between them.
    """

    def __init__(
        self, *, logger: Logger, unit_of_work_factory: Callable[[], AccountUnitOfWork]
    ) -> None:
        self._logger = logger
        self._new_unit_of_work = unit_of_work_factory

    async def execute(self, request: CloseAccountRequest) -> UserAccount:
        async with self._new_unit_of_work() as uow:
            # get(), not find(): AccountRepository's own find-or-raise already names the
            # reachable fact ("this id resolves to nothing") -- see transfer_money.py's own
            # comment on this exact point.
            account = await uow.accounts.get(criteria=FindAccountByAccountId(request.account_id))
            if not isinstance(account, UserAccount) or account.owner_id != request.caller_id:
                # ONE condition, not two sequential ifs: a caller who spoofs X-Caller-Id to
                # PLATFORM_OWNER_ID must not slip past a bare ownership-id comparison and then
                # hit a SYSTEM-shaped account somewhere downstream. This exact bug (an ownership
                # check and a type check done as two separate branches) was found and fixed twice
                # already on GetAccount/ListMovements -- not a third time.
                self._logger.warning(
                    "account %s is not owned by %s", request.account_id, request.caller_id
                )
                raise AccountOwnershipError(
                    f"account {request.account_id} is not owned by {request.caller_id}"
                )

            locked = await uow.accounts.get_for_update(request.account_id)
            if locked is None:  # pragma: no cover -- defensive: vanished between read and lock
                self._logger.error(
                    "account %s vanished between the unlocked read and the lock",
                    request.account_id,
                )
                raise RuntimeError(
                    f"account {request.account_id} vanished between the unlocked read and the lock"
                )

            closed = locked.close()
            await uow.accounts.update(closed)
            return closed
