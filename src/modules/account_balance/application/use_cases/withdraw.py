from dataclasses import dataclass
from logging import Logger

from modules.account_balance.application.gateways.account_repository import AccountRepository
from modules.account_balance.application.use_cases.system_account_resolver import (
    resolve_system_account,
)
from modules.account_balance.application.use_cases.transfer_money import (
    TransferMoney,
    TransferMoneyRequest,
)
from modules.account_balance.domain.account import AccountPurpose
from modules.account_balance.domain.identifiers import AccountId, IdempotencyKey, OwnerId
from modules.account_balance.domain.transfer import Transfer
from modules.shared.domain.money import Money


@dataclass(frozen=True, slots=True)
class WithdrawRequest:
    """`POST /withdrawals`'s use-case input -- states intent ("withdraw from X") rather than
    requiring the caller to know the platform's `SETTLEMENT` account id
    (openspec/specs/transfer/spec.md, "Known gap, decided, not yet built").
    """

    source_account_id: AccountId
    amount: Money
    idempotency_key: IdempotencyKey
    requested_by: OwnerId


class Withdraw:
    """A thin wrapper over `TransferMoney`, not a parallel use case: resolves the platform's
    `SETTLEMENT` account for the requested currency, then delegates locking, idempotency and
    authorization to the existing transfer path unchanged -- duplicating any of that here would
    duplicate exactly the concurrency-sensitive code `TransferMoney` already gets right (T1, T3,
    T5, T6).
    """

    def __init__(
        self,
        *,
        account_repository: AccountRepository,
        transfer_money: TransferMoney,
        logger: Logger,
    ) -> None:
        self._account_repository = account_repository
        self._transfer_money = transfer_money
        self._logger = logger

    async def execute(self, request: WithdrawRequest) -> Transfer:
        settlement_account = await resolve_system_account(
            repository=self._account_repository,
            purpose=AccountPurpose.SETTLEMENT,
            currency=request.amount.currency,
            logger=self._logger,
        )
        return await self._transfer_money.execute(
            TransferMoneyRequest(
                source_account_id=request.source_account_id,
                destination_account_id=settlement_account.account_id,
                amount=request.amount,
                idempotency_key=request.idempotency_key,
                requested_by=request.requested_by,
            )
        )
