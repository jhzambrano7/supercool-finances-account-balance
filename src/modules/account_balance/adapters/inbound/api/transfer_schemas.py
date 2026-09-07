from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from modules.account_balance.domain.entry import EntryDirection
from modules.account_balance.domain.transfer import Transfer


class TransferRequest(BaseModel):
    """`POST /transfers`'s body -- covers transfer, deposit and withdraw

    alike (spec Purpose: no separate concept for any of the three). Amount
    is minor units, matching `Money`; positivity and currency agreement are
    the domain's own guards (`NonPositiveAmountError`, `CurrencyMismatchError`),
    not re-validated here.
    """

    source_account_id: UUID
    destination_account_id: UUID
    amount: int
    currency: str = Field(min_length=3, max_length=3)


class EntryResponse(BaseModel):
    entry_id: UUID
    account_id: UUID
    direction: EntryDirection
    amount: int


class TransferResponse(BaseModel):
    """The HTTP representation of a posted `Transfer` -- reconstructed from

    the ledger on every path, including an idempotent replay (T3, T4): there
    is no separate "replay" shape.
    """

    transfer_id: UUID
    source_account_id: UUID
    destination_account_id: UUID
    amount: int
    currency: str
    requested_by: UUID
    occurred_at: datetime
    entries: list[EntryResponse]

    @classmethod
    def from_transfer(cls, transfer: Transfer) -> TransferResponse:
        return cls(
            transfer_id=transfer.transfer_id.value,
            source_account_id=transfer.source_account_id.value,
            destination_account_id=transfer.destination_account_id.value,
            amount=transfer.amount.amount,
            currency=str(transfer.amount.currency),
            requested_by=transfer.requested_by.value,
            occurred_at=transfer.occurred_at,
            entries=[
                EntryResponse(
                    entry_id=entry.entry_id.value,
                    account_id=entry.account_id.value,
                    direction=entry.direction,
                    amount=entry.amount.amount,
                )
                for entry in transfer.entries
            ],
        )
