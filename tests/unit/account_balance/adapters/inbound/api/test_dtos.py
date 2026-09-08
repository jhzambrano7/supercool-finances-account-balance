"""Unit tests for the inbound-API DTOs' own mapping logic -- same convention
as the DBOs' `from_domain`/`as_domain` (docs/coding-conventions.md): a DTO's
transformation is its own behavior and gets its own unit test, not only
indirect coverage through an HTTP integration test.
"""

from datetime import UTC, datetime
from uuid import uuid4

from modules.account_balance.adapters.inbound.api.dtos import (
    AccountResponseDto,
    TransferResponseDto,
)
from modules.account_balance.application.use_cases.account_register import OpenAccountResult
from modules.account_balance.domain.account import AccountPurpose, UserAccount
from modules.account_balance.domain.entry import Entry, EntryDirection
from modules.account_balance.domain.identifiers import (
    AccountId,
    EntryId,
    IdempotencyKey,
    OwnerId,
    TransferId,
)
from modules.account_balance.domain.transfer import Transfer
from modules.shared.domain.money import Currency, Money

USD = Currency("USD")


def test_from_result_maps_every_field() -> None:
    account = UserAccount.open(
        account_id=AccountId(uuid4()),
        owner_id=OwnerId(uuid4()),
        purpose=AccountPurpose.CHECKING,
        currency=USD,
    )
    result = OpenAccountResult(account=account, created=True)

    dto = AccountResponseDto.from_result(result)

    assert dto.account_id == account.account_id.value
    assert dto.owner_id == account.owner_id.value
    assert dto.purpose is AccountPurpose.CHECKING
    assert dto.currency == "USD"
    assert dto.balance == 0
    assert dto.status is account.status


def test_from_transfer_maps_every_field() -> None:
    transfer_id = TransferId(uuid4())
    source_account_id = AccountId(uuid4())
    destination_account_id = AccountId(uuid4())
    requested_by = OwnerId(uuid4())
    occurred_at = datetime(2024, 1, 1, tzinfo=UTC)
    amount = Money(1_000, USD)

    debit = Entry(
        entry_id=EntryId(uuid4()),
        transfer_id=transfer_id,
        account_id=source_account_id,
        direction=EntryDirection.DEBIT,
        amount=amount,
        occurred_at=occurred_at,
    )
    credit = Entry(
        entry_id=EntryId(uuid4()),
        transfer_id=transfer_id,
        account_id=destination_account_id,
        direction=EntryDirection.CREDIT,
        amount=amount,
        occurred_at=occurred_at,
    )
    transfer = Transfer(
        transfer_id=transfer_id,
        source_account_id=source_account_id,
        destination_account_id=destination_account_id,
        amount=amount,
        requested_by=requested_by,
        idempotency_key=IdempotencyKey("a-request-key"),
        occurred_at=occurred_at,
        entries=(debit, credit),
    )

    dto = TransferResponseDto.from_transfer(transfer)

    assert dto.transfer_id == transfer_id.value
    assert dto.source_account_id == source_account_id.value
    assert dto.destination_account_id == destination_account_id.value
    assert dto.amount == 1_000
    assert dto.currency == "USD"
    assert dto.requested_by == requested_by.value
    assert dto.occurred_at == occurred_at
    assert len(dto.entries) == 2

    debit_dto, credit_dto = dto.entries
    assert debit_dto.entry_id == debit.entry_id.value
    assert debit_dto.account_id == source_account_id.value
    assert debit_dto.direction is EntryDirection.DEBIT
    assert debit_dto.amount == 1_000
    assert credit_dto.entry_id == credit.entry_id.value
    assert credit_dto.account_id == destination_account_id.value
    assert credit_dto.direction is EntryDirection.CREDIT
    assert credit_dto.amount == 1_000
