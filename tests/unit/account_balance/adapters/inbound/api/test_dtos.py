"""Unit tests for the inbound-API DTOs' own mapping logic -- same convention
as the DBOs' `from_domain`/`as_domain` (docs/coding-conventions.md): a DTO's
transformation is its own behavior and gets its own unit test, not only
indirect coverage through an HTTP integration test.
"""

from uuid import uuid4

from modules.account_balance.adapters.inbound.api.dtos import AccountResponseDto
from modules.account_balance.application.use_cases.account_register import OpenAccountResult
from modules.account_balance.domain.account import Account, AccountPurpose, AccountType
from modules.account_balance.domain.identifiers import AccountId, OwnerId
from modules.shared.domain.money import Currency

USD = Currency("USD")


def test_from_result_maps_every_field() -> None:
    account = Account.open(
        account_id=AccountId(uuid4()),
        owner_id=OwnerId(uuid4()),
        account_type=AccountType.USER,
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
