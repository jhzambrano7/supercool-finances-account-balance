from modules.account_balance.application.gateways.account_repository import AccountRepository
from modules.account_balance.application.gateways.models.find_accounts_criteria import (
    FindAccountByAccountId,
)
from modules.account_balance.domain.account import UserAccount
from modules.account_balance.domain.errors import AccountOwnershipError
from modules.account_balance.domain.identifiers import AccountId, OwnerId


class GetAccount:
    """Reads one account, for its owner only (docs/web-ui-plan.md §6.1).

    Stricter than `TransferMoney`'s authorization rule (either leg's owner may transfer), and
    correct here: this is the endpoint that returns a balance. A `SYSTEM` account id fails the
    ownership check the same way a stranger's account would -- it is owned by `PLATFORM_OWNER_ID`,
    which no real caller's `owner_id` ever equals -- so "a `SYSTEM` account has no balance" (T7)
    stays true without a second, SYSTEM-specific check.
    """

    def __init__(self, *, repository: AccountRepository) -> None:
        self._repository = repository

    async def execute(self, *, account_id: AccountId, caller_id: OwnerId) -> UserAccount:
        account = await self._repository.get(criteria=FindAccountByAccountId(account_id))
        if account.owner_id != caller_id:
            raise AccountOwnershipError(f"account {account_id} is not owned by {caller_id}")
        if not isinstance(account, UserAccount):  # pragma: no cover -- defensive, mirrors AO4
            raise RuntimeError(
                f"account {account_id} passed the ownership check but is not a UserAccount -- "
                "the ownership check above should already exclude a SYSTEM account (its owner "
                "is PLATFORM_OWNER_ID, never a real caller)"
            )
        return account
