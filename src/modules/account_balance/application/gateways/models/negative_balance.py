from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from modules.account_balance.domain.identifiers import AccountId, OwnerId
from modules.shared.domain.money import Currency, Money


@dataclass(frozen=True, slots=True)
class NegativeBalanceAccount:
    """One `USER` account currently carrying a negative balance (PRD §11.3) -- the credit exposure
    a reversal can create (§7.3). Not a domain entity: a read projection over `accounts` and
    `entries`, existing only to answer "who owes us money, how much, and since when" -- the same
    reasoning `Movement` already gives for its own existence.

    `negative_since` is the start of the *current* negative episode, not the first time this
    account ever went negative -- an account that recovered (a deposit brought it back to >= 0)
    and later went negative again reports the second episode's start. See
    `queries/negative_balances.py` (the module that actually builds the window-function query --
    `sql_collections_repository.py` only executes it) for how that is computed and why a naive
    "oldest debit" or "first time it crossed zero" would be wrong.

    `negative_since` is `None` when the account is negative in `accounts.balance_amount` but no
    entry history explains it -- a materialized-balance drift (PRD §11.1), not the common case.
    Reporting `None` rather than inventing a timestamp is deliberate: this is exactly the situation
    the reconciliation check (`tests/integration/account_balance/test_reconciliation.py`) exists to
    catch, and this report must make it visible, not paper over it with a guess.
    """

    account_id: AccountId
    owner_id: OwnerId
    balance: Money
    negative_since: datetime | None


@dataclass(frozen=True, slots=True)
class CurrencyExposure:
    """Negative-balance exposure for one currency (PRD §11.3's "total of negative USER balances").

    Scoped to a single currency rather than one grand total: `Money.__add__` refuses to sum two
    different currencies (design intent, not an oversight -- see `shared/domain/money.py`), and a
    ledger that supports more than one currency (PRD §8) can have negative balances in more than
    one. A currency-blind "total owed" would either silently pick one currency's units to report
    the others in, or crash the first time a second currency has a negative balance -- this report
    never has to choose either.
    """

    currency: Currency
    count: int
    total_owed: Money  # positive magnitude -- how much is owed, not the (negative) balance itself


class NegativeAgeBucket(Enum):
    """Descriptive age buckets for a negative balance -- **not** a write-off policy.

    PRD §12 leaves "how long before a negative balance is written off" an explicitly open
    question. The two boundaries here (a day, a month) are the exact two points PRD §11.3 itself
    names as illustrative ("negative for a day is a collections case; negative for a month is a
    write-off nobody decided on") -- they exist so an operator can eyeball the shape of the
    exposure, not to make the write-off decision the PRD deliberately left unmade.
    """

    UNDER_ONE_DAY = "UNDER_ONE_DAY"
    ONE_TO_SEVEN_DAYS = "ONE_TO_SEVEN_DAYS"
    SEVEN_TO_THIRTY_DAYS = "SEVEN_TO_THIRTY_DAYS"
    OVER_THIRTY_DAYS = "OVER_THIRTY_DAYS"


# Fixed, ascending order -- the one place that order is decided, so the use case (building the
# histogram) and the DTO (rendering it) cannot disagree about it.
NEGATIVE_AGE_BUCKET_ORDER: tuple[NegativeAgeBucket, ...] = (
    NegativeAgeBucket.UNDER_ONE_DAY,
    NegativeAgeBucket.ONE_TO_SEVEN_DAYS,
    NegativeAgeBucket.SEVEN_TO_THIRTY_DAYS,
    NegativeAgeBucket.OVER_THIRTY_DAYS,
)


@dataclass(frozen=True, slots=True)
class CollectionsReport:
    """The operator-facing answer to "who is negative, by how much, and for how long" (PRD §11.3).

    `as_of` is the one clock read this whole report is built against -- every account's age is
    `as_of - negative_since`, so two rows in the same response can never be aged against two
    different instants (design D6: the application layer, not each call site, decides "now").

    `accounts` orders every account with an unexplained (`negative_since is None`) balance first --
    a materialized-balance drift outranks even the oldest ordinary collections case, since it is a
    data-integrity question, not a customer one -- then the rest oldest-`negative_since`-first,
    matching PRD §11.3's own framing ("a balance negative for a day is a collections case").
    """

    as_of: datetime
    accounts: tuple[NegativeBalanceAccount, ...]
    exposures: tuple[CurrencyExposure, ...]
    age_buckets: Mapping[NegativeAgeBucket, int]

    @property
    def count(self) -> int:
        return len(self.accounts)

    @property
    def unexplained_count(self) -> int:
        """How many negative accounts have no entry history explaining them (PRD §11.1's balance
        drift, surfaced here rather than silently excluded -- see `NegativeBalanceAccount`'s own
        docstring)."""
        return sum(1 for account in self.accounts if account.negative_since is None)

    @property
    def oldest_negative_since(self) -> datetime | None:
        """The oldest *known* negative-episode start, or `None` when there are no negative
        balances or none of them has a known start. An unexplained account (`negative_since is
        None`) contributes nothing here -- there is no timestamp to compare, and inventing one
        (e.g. treating it as infinitely old) would misstate what this field actually means."""
        known = [
            account.negative_since
            for account in self.accounts
            if account.negative_since is not None
        ]
        return min(known) if known else None
