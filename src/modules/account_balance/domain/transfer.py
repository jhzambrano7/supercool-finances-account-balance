from dataclasses import dataclass
from datetime import datetime

from modules.account_balance.domain.entry import Entry, EntryDirection
from modules.account_balance.domain.errors import (
    MalformedTransferError,
    NaiveTimestampError,
    NonPositiveAmountError,
    SelfTransferError,
    UnbalancedTransferError,
)
from modules.account_balance.domain.identifiers import (
    AccountId,
    IdempotencyKey,
    OwnerId,
    TransferId,
)
from modules.shared.domain.money import Currency, Money


@dataclass(frozen=True, slots=True)
class Transfer:
    """A posted money movement: frozen, carries no status (D9).

    `entries` is `tuple[Entry, ...]`, not a 2-tuple — a future FX posting (or
    an unaffordable-reversal posting) adds legs without reshaping this type.
    I1 is enforced here, at construction, as per-currency netting (design
    §5): in v1 (one currency, two legs) this degenerates exactly to
    `sum(debits) == sum(credits)`, but the general form is what survives the
    extension, so no `Transfer` — hand-built or repository-reconstituted —
    can ever exist unbalanced.
    """

    transfer_id: TransferId
    source_account_id: AccountId
    destination_account_id: AccountId
    amount: Money
    requested_by: OwnerId
    idempotency_key: IdempotencyKey
    occurred_at: datetime
    entries: tuple[Entry, ...]
    reverses: TransferId | None = None

    def __post_init__(self) -> None:
        """Guards run in this exact order (design §5) because more than one can be true of a
        malformed input at once, and only one error should be raised: positivity, then self-
        transfer, then leg shape, then the timestamp, then I1's per-currency netting."""
        if not self.amount.is_positive:
            raise NonPositiveAmountError(
                f"transfer amount must be strictly positive, got {self.amount}"
            )
        if self.source_account_id == self.destination_account_id:
            raise SelfTransferError(
                f"source and destination account must differ, both are {self.source_account_id}"
            )
        if (
            len(self.entries) < 2
            or any(entry.transfer_id != self.transfer_id for entry in self.entries)
            or not any(
                entry.account_id == self.source_account_id
                and entry.direction is EntryDirection.DEBIT
                for entry in self.entries
            )
            or not any(
                entry.account_id == self.destination_account_id
                and entry.direction is EntryDirection.CREDIT
                for entry in self.entries
            )
        ):
            raise MalformedTransferError(
                f"transfer {self.transfer_id} legs do not describe its own movement"
            )
        if self.occurred_at.tzinfo is None:
            raise NaiveTimestampError(
                "transfer.occurred_at must be timezone-aware, got a naive datetime"
            )
        totals: dict[Currency, Money] = {}
        for entry in self.entries:
            currency = entry.amount.currency
            totals[currency] = totals.get(currency, Money.zero(currency)) + entry.signed_amount
        if any(not total.is_zero for total in totals.values()):
            raise UnbalancedTransferError(
                f"transfer {self.transfer_id} legs do not net to zero per currency: {totals}"
            )
