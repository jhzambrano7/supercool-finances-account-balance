from logging import Logger

from modules.account_balance.application.gateways.account_repository import AccountRepository
from modules.account_balance.application.gateways.models.find_accounts_criteria import (
    FindSystemAccountByPurposeAndCurrency,
)
from modules.account_balance.domain.account import AccountPurpose, SystemAccount
from modules.shared.application.errors import ApplicationError
from modules.shared.domain.money import Currency


class CurrencyNotOperationalError(ApplicationError):
    """A `Currency` that is valid but has no seeded `FUNDING`/`SETTLEMENT` account yet.

    Distinct from `AccountNotFoundError`: the caller's request named a real, well-formed
    currency -- the gap is a platform-configuration one ("we do not operate this currency"), not
    a client mistake. Maps to `503` (openspec/specs/transfer/spec.md, "The design, settled"), not
    the `400`/`422` this slice's other errors use.
    """

    def __init__(self, *, purpose: AccountPurpose, currency: Currency) -> None:
        self.purpose = purpose
        self.currency = currency
        super().__init__(f"no {purpose.value} account is seeded for currency {currency}")


class SystemAccountResolver:
    """Shared by `Deposit` and `Withdraw`: looks up the platform's one `FUNDING`/`SETTLEMENT`
    account for a currency, or raises `CurrencyNotOperationalError` if none is seeded yet.

    A class with its dependencies injected (`repository`, `logger`), not a free function --
    mirrors this module's own `IdGenerator`/`Clock` shape (`application/services/`): one small,
    focused responsibility, supplied to whatever needs it rather than reconstructed per call.
    """

    def __init__(self, *, repository: AccountRepository, logger: Logger) -> None:
        self._repository = repository
        self._logger = logger

    async def resolve(self, *, purpose: AccountPurpose, currency: Currency) -> SystemAccount:
        account = await self._repository.find(
            criteria=FindSystemAccountByPurposeAndCurrency(purpose=purpose, currency=currency)
        )
        if account is None:
            # Log before raising (docs/coding-conventions.md, application layer only): a 503 here
            # is a platform-configuration gap, not a client mistake -- on-call needs a trail to
            # grep for, matching how transfer_money.py logs every one of its own raises.
            self._logger.warning("no %s account is seeded for currency %s", purpose.value, currency)
            raise CurrencyNotOperationalError(purpose=purpose, currency=currency)
        if not isinstance(account, SystemAccount):  # pragma: no cover -- defensive, mirrors AO4
            self._logger.error(
                "FindSystemAccountByPurposeAndCurrency(%s, %s) matched a non-SYSTEM account %s",
                purpose.value,
                currency,
                account.account_id,
            )
            raise RuntimeError(
                f"FindSystemAccountByPurposeAndCurrency({purpose.value}, {currency}) matched a "
                f"non-SYSTEM account {account.account_id}"
            )
        return account
