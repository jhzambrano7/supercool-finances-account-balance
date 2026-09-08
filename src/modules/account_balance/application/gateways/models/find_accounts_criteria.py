from dataclasses import dataclass

from modules.account_balance.domain.account import AccountPurpose, AccountType
from modules.account_balance.domain.errors import InvalidAccountPurposeError
from modules.account_balance.domain.identifiers import AccountId, OwnerId
from modules.shared.domain.money import Currency


@dataclass(frozen=True, slots=True)
class FindAccountByOwnerAndPurposeAndCurrency:
    owner_id: OwnerId
    purpose: AccountPurpose
    currency: Currency


@dataclass(frozen=True, slots=True)
class FindAccountByAccountId:
    account_id: AccountId


@dataclass(frozen=True, slots=True)
class FindSystemAccountByPurposeAndCurrency:
    """Resolves the platform's `FUNDING`/`SETTLEMENT` account for a currency -- what `deposit`/
    `withdraw` need instead of a caller-supplied account id (`openspec/specs/transfer/spec.md`,
    "The design, settled").

    Deliberately narrower than `FindAccountByOwnerAndPurposeAndCurrency`: without an `owner_id`,
    `(purpose, currency)` is only guaranteed to match exactly one row for a `SYSTEM` purpose (one
    `PLATFORM_OWNER_ID`-owned account per currency) -- a `USER` purpose has no such guarantee, many
    owners hold one each. Validating that here, at construction, turns a same-shaped-criteria
    misuse into a type error instead of a query silently returning an arbitrary match.
    """

    purpose: AccountPurpose
    currency: Currency

    def __post_init__(self) -> None:
        if not self.purpose.matches_type(AccountType.SYSTEM):
            raise InvalidAccountPurposeError(
                f"{self.purpose.value} does not belong to {AccountType.SYSTEM.value}, "
                "so it cannot be looked up via FindSystemAccountByPurposeAndCurrency"
            )


FindAccountCriteria = (
    FindAccountByOwnerAndPurposeAndCurrency
    | FindAccountByAccountId
    | FindSystemAccountByPurposeAndCurrency
)
