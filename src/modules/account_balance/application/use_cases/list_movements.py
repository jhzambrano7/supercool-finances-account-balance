from logging import Logger

from modules.account_balance.application.gateways.account_repository import AccountRepository
from modules.account_balance.application.gateways.models.find_accounts_criteria import (
    FindAccountByAccountId,
)
from modules.account_balance.application.gateways.models.movement import MovementPage
from modules.account_balance.application.gateways.movement_repository import MovementRepository
from modules.account_balance.domain.account import UserAccount
from modules.account_balance.domain.errors import AccountOwnershipError
from modules.account_balance.domain.identifiers import AccountId, OwnerId


class ListMovements:
    """Reads one page of an account's ledger movements, for its owner only
    (docs/web-ui-plan.md §6.2) -- `SYSTEM` movement history is an operator concern and is not
    requested here, so this endpoint reads a `USER` account only. A `SYSTEM` account's owner is
    `PLATFORM_OWNER_ID`, which no real caller legitimately has -- but `X-Caller-Id` is a bare,
    unvalidated UUID header (T2), so a caller can still *spoof* it to `PLATFORM_OWNER_ID` and pass
    the ownership check on identity alone. The `isinstance` check below is not defensive dead code:
    it is what actually keeps this endpoint reading only `USER` accounts, and a caller who reaches
    it this way is refused exactly like any other non-owner, not treated as a server fault.
    """

    def __init__(
        self,
        *,
        account_repository: AccountRepository,
        movement_repository: MovementRepository,
        logger: Logger,
    ) -> None:
        self._account_repository = account_repository
        self._movement_repository = movement_repository
        self._logger = logger

    async def execute(
        self, *, account_id: AccountId, caller_id: OwnerId, limit: int, cursor: str | None
    ) -> MovementPage:
        account = await self._account_repository.get(criteria=FindAccountByAccountId(account_id))
        if account.owner_id != caller_id or not isinstance(account, UserAccount):
            # Log before raising (docs/coding-conventions.md, application layer only): a rejected
            # read is worth a trail, matching every other expected-ish rejection in this codebase.
            self._logger.warning("account %s is not owned by %s", account_id, caller_id)
            raise AccountOwnershipError(f"account {account_id} is not owned by {caller_id}")
        return await self._movement_repository.find_by_account(
            account_id=account_id, limit=limit, cursor=cursor
        )
