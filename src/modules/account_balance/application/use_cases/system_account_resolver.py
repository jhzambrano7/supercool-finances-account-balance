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


async def resolve_system_account(
    *, repository: AccountRepository, purpose: AccountPurpose, currency: Currency
) -> SystemAccount:
    """Shared by `Deposit` and `Withdraw`: looks up the platform's one `FUNDING`/`SETTLEMENT`
    account for `currency`, or raises `CurrencyNotOperationalError` if none is seeded yet.
    """
    account = await repository.find(
        criteria=FindSystemAccountByPurposeAndCurrency(purpose=purpose, currency=currency)
    )
    if account is None:
        raise CurrencyNotOperationalError(purpose=purpose, currency=currency)
    assert isinstance(account, SystemAccount), (
        "FindSystemAccountByPurposeAndCurrency only ever matches a SYSTEM row"
    )
    return account
