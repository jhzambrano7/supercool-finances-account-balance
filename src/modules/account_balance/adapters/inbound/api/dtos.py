from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from modules.account_balance.application.use_cases.account_register import OpenAccountResult
from modules.account_balance.domain.account import AccountPurpose, AccountStatus
from modules.account_balance.domain.entry import EntryDirection
from modules.account_balance.domain.transfer import Transfer


class OpenAccountRequestDto(BaseModel):
    """`POST /accounts` body.

    `account_type` is deliberately absent (AO1) — this endpoint only opens
    `USER` accounts; `SYSTEM` accounts are platform-seeded infrastructure and
    are never opened by a customer request.
    """

    owner_id: UUID
    purpose: AccountPurpose
    currency: str = Field(min_length=3, max_length=3)


class AccountResponseDto(BaseModel):
    """The HTTP representation of an account — not passed into the use case or the domain in the
    other direction either."""

    account_id: UUID
    owner_id: UUID
    purpose: AccountPurpose
    currency: str
    balance: int
    status: AccountStatus

    @classmethod
    def from_result(cls, result: OpenAccountResult) -> AccountResponseDto:
        account = result.account
        return cls(
            account_id=account.account_id.value,
            owner_id=account.owner_id.value,
            purpose=account.purpose,
            currency=str(account.currency),
            balance=account.balance.amount,
            status=account.status,
        )


class TransferRequestDto(BaseModel):
    """`POST /transfers`'s body -- covers transfer, deposit and withdraw alike (spec Purpose).

    There is no separate concept for any of the three. Amount is minor units,
    matching `Money`; positivity and currency agreement are the domain's own
    guards (`NonPositiveAmountError`, `CurrencyMismatchError`), not
    re-validated here.
    """

    source_account_id: UUID
    destination_account_id: UUID
    amount: int
    currency: str = Field(min_length=3, max_length=3)


class EntryResponseDto(BaseModel):
    entry_id: UUID
    account_id: UUID
    direction: EntryDirection
    amount: int


class TransferResponseDto(BaseModel):
    """The HTTP representation of a posted `Transfer`, reconstructed from the ledger on every path.

    This includes an idempotent replay (T3, T4): there is no separate
    "replay" shape.
    """

    transfer_id: UUID
    source_account_id: UUID
    destination_account_id: UUID
    amount: int
    currency: str
    requested_by: UUID
    occurred_at: datetime
    entries: list[EntryResponseDto]

    @classmethod
    def from_transfer(cls, transfer: Transfer) -> TransferResponseDto:
        return cls(
            transfer_id=transfer.transfer_id.value,
            source_account_id=transfer.source_account_id.value,
            destination_account_id=transfer.destination_account_id.value,
            amount=transfer.amount.amount,
            currency=str(transfer.amount.currency),
            requested_by=transfer.requested_by.value,
            occurred_at=transfer.occurred_at,
            entries=[
                EntryResponseDto(
                    entry_id=entry.entry_id.value,
                    account_id=entry.account_id.value,
                    direction=entry.direction,
                    amount=entry.amount.amount,
                )
                for entry in transfer.entries
            ],
        )
