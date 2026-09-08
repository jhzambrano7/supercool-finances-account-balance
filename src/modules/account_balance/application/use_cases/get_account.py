from logging import Logger

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
    correct here: this is the endpoint that returns a balance. A `SYSTEM` account's owner is
    `PLATFORM_OWNER_ID`, which no real caller legitimately has -- but `X-Caller-Id` is a bare,
    unvalidated UUID header (T2), so a caller can still *spoof* it to `PLATFORM_OWNER_ID` and pass
    the ownership check on identity alone. The `isinstance` check below is not defensive dead
    code: it is what actually keeps a `SYSTEM` account's non-existent balance (T7) from ever
    reaching `AccountResponseDto`, and a caller who reaches it this way is refused exactly like
    any other non-owner, not treated as a server fault.
    """

    def __init__(self, *, repository: AccountRepository, logger: Logger) -> None:
        self._repository = repository
        self._logger = logger

    async def execute(self, *, account_id: AccountId, caller_id: OwnerId) -> UserAccount:
        account = await self._repository.get(criteria=FindAccountByAccountId(account_id))
        if account.owner_id != caller_id or not isinstance(account, UserAccount):
            # Log before raising (docs/coding-conventions.md, application layer only): a rejected
            # read is worth a trail, matching every other expected-ish rejection in this codebase.
            self._logger.warning("account %s is not owned by %s", account_id, caller_id)
            raise AccountOwnershipError(f"account {account_id} is not owned by {caller_id}")
        return account
