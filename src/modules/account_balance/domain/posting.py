from dataclasses import dataclass
from datetime import datetime

from modules.account_balance.domain.account import Account
from modules.account_balance.domain.entry import Entry, EntryDirection
from modules.account_balance.domain.errors import ReversalMismatchError, SelfTransferError
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


def revert(
    original: Transfer,
    *,
    transfer_id: TransferId,
    source: Account,
    destination: Account,
    requested_by: OwnerId,
    idempotency_key: IdempotencyKey,
    occurred_at: datetime,
    entry_ids: tuple[EntryId, EntryId],
) -> Posting:
    """Posts a reversal of `original`. Identical to `transfer` with three

    differences (design §5.2):

    1. `amount` is not a parameter -- it is `original.amount`. A partial
       reversal is just another transfer and does not need a concept (D10).
    2. Guards `source.account_id == original.destination_account_id` and
       `destination.account_id == original.source_account_id`, else
       `ReversalMismatchError` -- the caller loaded the wrong rows. This
       guard alone is what makes a standalone self-transfer/positivity check
       unnecessary here: passing it implies `source != destination` (the
       original could not have been a self-transfer) and a positive amount
       (inherited from `original`, itself already validated).
    3. Applies via `source.debit_for_reversal(...)`, not `source.debit(...)`
       -- the sole call site outside `account.py` itself (design §4.3's
       architecture test) -- so the reversal succeeds in full even when it
       drives `source` (the original's destination) below zero (PRD §7.3).

    `original` is never touched: reverting reads its `amount`,
    `destination_account_id`, `source_account_id` and `transfer_id`, and
    constructs an entirely new `Transfer` carrying `reverses=original.
    transfer_id` (I7).
    """
    if (
        source.account_id != original.destination_account_id
        or destination.account_id != original.source_account_id
    ):
        raise ReversalMismatchError(
            f"revert() accounts do not mirror transfer {original.transfer_id}: "
            f"expected source={original.destination_account_id}, "
            f"destination={original.source_account_id}, "
            f"got source={source.account_id}, destination={destination.account_id}"
        )

    amount = original.amount

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

    debited = source.debit_for_reversal(debit_leg)
    credited = destination.credit(credit_leg)

    reversal = Transfer(
        transfer_id=transfer_id,
        source_account_id=source.account_id,
        destination_account_id=destination.account_id,
        amount=amount,
        requested_by=requested_by,
        idempotency_key=idempotency_key,
        occurred_at=occurred_at,
        entries=(debit_leg, credit_leg),
        reverses=original.transfer_id,
    )

    return Posting(transfer=reversal, accounts=(debited, credited))
