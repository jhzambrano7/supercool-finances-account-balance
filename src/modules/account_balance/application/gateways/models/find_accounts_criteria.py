from dataclasses import dataclass

from modules.account_balance.domain.account import AccountPurpose
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


FindAccountCriteria = FindAccountByOwnerAndPurposeAndCurrency | FindAccountByAccountId
