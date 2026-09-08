from modules.account_balance.application.gateways.account_repository import AccountRepository
from modules.account_balance.domain.account import UserAccount
from modules.account_balance.domain.identifiers import OwnerId


class ListAccounts:
    """Lists every account the caller owns (docs/web-ui-plan.md §6.1b) -- no query parameters,
    the owner is the caller, no pagination (the port's own `find_by_owner` explains why one is
    not needed).
    """

    def __init__(self, *, repository: AccountRepository) -> None:
        self._repository = repository

    async def execute(self, *, caller_id: OwnerId) -> tuple[UserAccount, ...]:
        return await self._repository.find_by_owner(caller_id)
