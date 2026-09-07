from dataclasses import dataclass
from datetime import datetime

from modules.account_balance.domain.account import Account
from modules.account_balance.domain.entry import Entry, EntryDirection
from modules.account_balance.domain.errors import SelfTransferError
from modules.account_balance.domain.identifiers import (
    EntryId,
    IdempotencyKey,
    OwnerId,
    TransferId,
)
from modules.account_balance.domain.transfer import Transfer
from modules.shared.domain.errors import CurrencyMismatchError
from modules.shared.domain.money import Money


@dataclass(frozen=True, slots=True)
class Posting:
    """A posted `Transfer` plus the successor `Account` instances it produced.

    `Account` is immutable — `credit`/`debit`/`debit_for_reversal` each return
    a *new* instance rather than mutating the receiver — so a caller given
    only the `Transfer` would have nothing to persist the new balances from.
    `Posting` carries both (design §5.1).
    """

    transfer: Transfer
    accounts: tuple[Account, ...]


def transfer(
    *,
    transfer_id: TransferId,
    source: Account,
    destination: Account,
    amount: Money,
    requested_by: OwnerId,
    idempotency_key: IdempotencyKey,
    occurred_at: datetime,
    entry_ids: tuple[EntryId, EntryId],
) -> Posting:
    """Posts an ordinary transfer: two balanced legs, applied atomically in-process.

    Sequence, and the order is the whole point (design §5.1): guard (self-
    transfer, then I4 currency agreement across source/destination/amount) ->
    build the two legs -> apply them to the accounts (I2 fires on the debit)
    -> construct `Transfer` (I1 fires here) -> return `Posting`. Every rule
    that can refuse has refused before any `Account.debit`/`credit` call
    succeeds, so a raise here always leaves both accounts exactly as they
    were passed in -- nothing is undone because nothing was done.

    Positivity (I3) is not re-guarded here: `Entry.__post_init__` already
    raises `NonPositiveAmountError` when the two legs are built (step 2,
    below), which is the same exception a duplicate guard at step 1 would
    raise -- adding one would be untestably-identical code, not a behaviour
    change.
    """
    if source.account_id == destination.account_id:
        raise SelfTransferError(
            f"source and destination account must differ, both are {source.account_id}"
        )
    if source.currency != destination.currency or source.currency != amount.currency:
        raise CurrencyMismatchError(
            f"source ({source.currency}), destination ({destination.currency}) and amount "
            f"({amount.currency}) currencies must all match"
        )

    debit_leg = Entry(
        entry_id=entry_ids[0],
        transfer_id=transfer_id,
        account_id=source.account_id,
        direction=EntryDirection.DEBIT,
        amount=amount,
        occurred_at=occurred_at,
    )
    credit_leg = Entry(
        entry_id=entry_ids[1],
        transfer_id=transfer_id,
        account_id=destination.account_id,
        direction=EntryDirection.CREDIT,
        amount=amount,
        occurred_at=occurred_at,
    )

    debited = source.debit(debit_leg)
    credited = destination.credit(credit_leg)

    posted = Transfer(
        transfer_id=transfer_id,
        source_account_id=source.account_id,
        destination_account_id=destination.account_id,
        amount=amount,
        requested_by=requested_by,
        idempotency_key=idempotency_key,
        occurred_at=occurred_at,
        entries=(debit_leg, credit_leg),
    )

    return Posting(transfer=posted, accounts=(debited, credited))
