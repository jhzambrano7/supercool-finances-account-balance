from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from modules.account_balance.domain.errors import NaiveTimestampError, NonPositiveAmountError
from modules.account_balance.domain.identifiers import AccountId, EntryId, TransferId
from modules.shared.domain.money import Money


class EntryDirection(Enum):
    """Which way a leg moves money, as a first-class term (§4.2).

    String values, not `auto()`: this is persisted, so a reordering of the
    members must not silently reinterpret a stored row.
    """

    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


@dataclass(frozen=True, slots=True)
class Entry:
    """One leg of a `Transfer`: one account, one direction, one positive amount.

    The amount is never signed at the source — it is always strictly
    positive, with an explicit `direction` alongside it. `signed_amount` is
    the *only* place a sign is derived from that direction (D2); `Account`
    never inspects `direction` itself, it only adds `signed_amount`. That
    keeps the sign decided in exactly one place instead of two that could
    disagree.
    """

    entry_id: EntryId
    transfer_id: TransferId
    account_id: AccountId
    direction: EntryDirection
    amount: Money
    occurred_at: datetime

    def __post_init__(self) -> None:
        if not self.amount.is_positive:
            raise NonPositiveAmountError(
                f"entry amount must be strictly positive, got {self.amount}"
            )
        if self.occurred_at.tzinfo is None:
            raise NaiveTimestampError(
                "entry.occurred_at must be timezone-aware, got a naive datetime"
            )

    @property
    def signed_amount(self) -> Money:
        if self.direction is EntryDirection.CREDIT:
            return self.amount
        return -self.amount
