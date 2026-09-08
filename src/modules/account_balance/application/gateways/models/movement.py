from dataclasses import dataclass
from datetime import datetime

from modules.account_balance.domain.entry import EntryDirection
from modules.account_balance.domain.identifiers import AccountId, EntryId, OwnerId, TransferId
from modules.shared.domain.money import Money


@dataclass(frozen=True, slots=True)
class Movement:
    """One statement line for an account -- what happened *to it*, from its own point of view
    (docs/web-ui-plan.md §6.2). Not a domain entity: a read projection joining an `Entry` leg with
    its owning `Transfer`, existing only to answer this one question, never persisted or otherwise
    round-tripped.
    """

    entry_id: EntryId
    transfer_id: TransferId
    direction: EntryDirection
    amount: Money
    counterparty_account_id: AccountId
    requested_by: OwnerId
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class MovementPage:
    """One page of `Movement`s, newest first. `next_cursor` is `None` exactly when this page
    reached the end of the account's history (docs/web-ui-plan.md §6.2: cursor pagination, not
    offset -- the ledger is append-only and read newest-first, so an offset would shift under the
    reader every time anything posts).
    """

    items: tuple[Movement, ...]
    next_cursor: str | None
