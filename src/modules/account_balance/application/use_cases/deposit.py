from dataclasses import dataclass

from modules.account_balance.application.services.system_account_resolver import (
    SystemAccountResolver,
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
class DepositRequest:
    """`POST /deposits`'s use-case input -- states intent ("deposit into Y") rather than requiring
    the caller to know the platform's `FUNDING` account id (openspec/specs/transfer/spec.md,
    "Known gap, decided, not yet built").
    """

    destination_account_id: AccountId
    amount: Money
    idempotency_key: IdempotencyKey
    requested_by: OwnerId


class Deposit:
    """A thin wrapper over `TransferMoney`, not a parallel use case: resolves the platform's
    `FUNDING` account for the requested currency, then delegates locking, idempotency and
    authorization to the existing transfer path unchanged -- duplicating any of that here would
    duplicate exactly the concurrency-sensitive code `TransferMoney` already gets right (T1, T3,
    T5, T6).
    """

    def __init__(
        self,
        *,
        system_account_resolver: SystemAccountResolver,
        transfer_money: TransferMoney,
    ) -> None:
        self._system_account_resolver = system_account_resolver
        self._transfer_money = transfer_money

    async def execute(self, request: DepositRequest) -> Transfer:
        funding_account = await self._system_account_resolver.resolve(
            purpose=AccountPurpose.FUNDING,
            currency=request.amount.currency,
        )
        return await self._transfer_money.execute(
            TransferMoneyRequest(
                source_account_id=funding_account.account_id,
                destination_account_id=request.destination_account_id,
                amount=request.amount,
                idempotency_key=request.idempotency_key,
                requested_by=request.requested_by,
            )
        )
