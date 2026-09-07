from uuid import UUID

from pydantic import BaseModel, Field

from modules.account_balance.application.use_cases.open_account import OpenAccountResult
from modules.account_balance.domain.account import AccountPurpose, AccountStatus


class OpenAccountRequest(BaseModel):
    """`POST /accounts` body.

    `account_type` is deliberately absent (AO1) — this endpoint only opens
    `USER` accounts; `SYSTEM` accounts are platform-seeded infrastructure and
    are never opened by a customer request.
    """

    owner_id: UUID
    purpose: AccountPurpose
    currency: str = Field(min_length=3, max_length=3)


class AccountResponse(BaseModel):
    """The HTTP representation of an account — not passed into the use case

    or the domain in the other direction either.
    """

    account_id: UUID
    owner_id: UUID
    purpose: AccountPurpose
    currency: str
    balance: int
    status: AccountStatus

    @classmethod
    def from_result(cls, result: OpenAccountResult) -> AccountResponse:
        account = result.account
        return cls(
            account_id=account.account_id.value,
            owner_id=account.owner_id.value,
            purpose=account.purpose,
            currency=str(account.currency),
            balance=account.balance.amount,
            status=account.status,
        )
