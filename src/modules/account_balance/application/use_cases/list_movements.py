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
    requested here, so this endpoint reads a `USER` account only. The ownership check below
    already excludes a `SYSTEM` account the same way `GetAccount` does (owned by
    `PLATFORM_OWNER_ID`, never a real caller), so there is no separate `AccountType` check.
    """

    def __init__(
        self, *, account_repository: AccountRepository, movement_repository: MovementRepository
    ) -> None:
        self._account_repository = account_repository
        self._movement_repository = movement_repository

    async def execute(
        self, *, account_id: AccountId, caller_id: OwnerId, limit: int, cursor: str | None
    ) -> MovementPage:
        account = await self._account_repository.get(criteria=FindAccountByAccountId(account_id))
        if account.owner_id != caller_id:
            raise AccountOwnershipError(f"account {account_id} is not owned by {caller_id}")
        if not isinstance(account, UserAccount):  # pragma: no cover -- defensive, mirrors AO4
            raise RuntimeError(
                f"account {account_id} passed the ownership check but is not a UserAccount -- "
                "the ownership check above should already exclude a SYSTEM account (its owner "
                "is PLATFORM_OWNER_ID, never a real caller)"
            )
        return await self._movement_repository.find_by_account(
            account_id=account_id, limit=limit, cursor=cursor
        )
