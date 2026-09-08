from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from modules.account_balance.application.gateways.models.movement import Movement, MovementPage
from modules.account_balance.application.use_cases.account_register import OpenAccountResult
from modules.account_balance.domain.account import AccountPurpose, AccountStatus, UserAccount
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
        # AO1: account-opening only ever produces a USER account -- SYSTEM
        # accounts are platform-seeded infrastructure, never returned here.
        assert isinstance(account, UserAccount), "account-opening never returns a SYSTEM account"
        return cls.from_account(account)

    @classmethod
    def from_account(cls, account: UserAccount) -> AccountResponseDto:
        return cls(
            account_id=account.account_id.value,
            owner_id=account.owner_id.value,
            purpose=account.purpose,
            currency=str(account.currency),
            balance=account.balance.amount,
            status=account.status,
        )


class AccountsResponseDto(BaseModel):
    """`GET /accounts`'s body (docs/web-ui-plan.md §6.1b) -- wrapped in `{items: [...]}` rather
    than a bare array, so a `next_cursor` could be added later without a breaking change, even
    though this endpoint has no pagination today (bounded by purpose x currency per owner).
    """

    items: list[AccountResponseDto]


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


class DepositRequestDto(BaseModel):
    """`POST /deposits`'s body -- states intent ("deposit into Y") instead of a raw
    `source_account_id`/`destination_account_id` pair; the platform's `FUNDING` account for
    `currency` is resolved by the use case, not supplied by the caller.
    """

    destination_account_id: UUID
    amount: int
    currency: str = Field(min_length=3, max_length=3)


class WithdrawalRequestDto(BaseModel):
    """`POST /withdrawals`'s body -- the deposit DTO's mirror image: the platform's `SETTLEMENT`
    account for `currency` is resolved by the use case, not supplied by the caller.
    """

    source_account_id: UUID
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


class MovementResponseDto(BaseModel):
    """`GET /accounts/{account_id}/movements`'s per-item shape (docs/web-ui-plan.md §6.2) --
    narrower than `EntryResponseDto` in one way (no bare `account_id`, since the caller already
    named the account in the path) and wider in another (`currency`, `counterparty_account_id`,
    `requested_by`, `occurred_at`, none of which `EntryResponseDto` carries) -- a distinct DTO
    rather than a variant of `EntryResponseDto`, which `TransferResponseDto.entries` already
    depends on with its current, narrower shape.
    """

    entry_id: UUID
    transfer_id: UUID
    direction: EntryDirection
    amount: int
    currency: str
    counterparty_account_id: UUID
    requested_by: UUID
    occurred_at: datetime

    @classmethod
    def from_movement(cls, movement: Movement) -> MovementResponseDto:
        return cls(
            entry_id=movement.entry_id.value,
            transfer_id=movement.transfer_id.value,
            direction=movement.direction,
            amount=movement.amount.amount,
            currency=str(movement.amount.currency),
            counterparty_account_id=movement.counterparty_account_id.value,
            requested_by=movement.requested_by.value,
            occurred_at=movement.occurred_at,
        )


class MovementsResponseDto(BaseModel):
    """`GET /accounts/{account_id}/movements`'s body -- cursor pagination, not offset
    (docs/web-ui-plan.md §6.2): `next_cursor` is `None` exactly when this page reached the end."""

    items: list[MovementResponseDto]
    next_cursor: str | None

    @classmethod
    def from_page(cls, page: MovementPage) -> MovementsResponseDto:
        return cls(
            items=[MovementResponseDto.from_movement(movement) for movement in page.items],
            next_cursor=page.next_cursor,
        )
