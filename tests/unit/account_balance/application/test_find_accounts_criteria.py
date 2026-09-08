"""Unit tests for `FindSystemAccountByPurposeAndCurrency`'s own construction-time validation.

Per openspec/specs/transfer/spec.md's "The design, settled": without an `owner_id`, `(purpose,
currency)` is only guaranteed unique for a SYSTEM purpose -- a USER purpose must be rejected here,
at construction, rather than silently accepted into a query that could match many rows.
"""

import pytest

from modules.account_balance.application.gateways.models.find_accounts_criteria import (
    FindSystemAccountByPurposeAndCurrency,
)
from modules.account_balance.domain.account import AccountPurpose
from modules.account_balance.domain.errors import InvalidAccountPurposeError
from modules.shared.domain.money import Currency

USD = Currency("USD")


@pytest.mark.parametrize("purpose", [AccountPurpose.FUNDING, AccountPurpose.SETTLEMENT])
def test_a_system_purpose_is_accepted(purpose: AccountPurpose) -> None:
    criteria = FindSystemAccountByPurposeAndCurrency(purpose=purpose, currency=USD)

    assert criteria.purpose is purpose
    assert criteria.currency == USD


@pytest.mark.parametrize("purpose", [AccountPurpose.CHECKING, AccountPurpose.SAVINGS])
def test_a_user_purpose_is_rejected_at_construction(purpose: AccountPurpose) -> None:
    with pytest.raises(InvalidAccountPurposeError):
        FindSystemAccountByPurposeAndCurrency(purpose=purpose, currency=USD)
