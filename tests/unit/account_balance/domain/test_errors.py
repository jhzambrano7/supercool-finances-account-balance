import pytest

from modules.account_balance.domain import errors as account_balance_errors
from modules.shared.domain.errors import CurrencyMismatchError, DomainError

NEW_ERROR_NAMES = (
    "InsufficientFundsError",
    "NonPositiveAmountError",
    "SelfTransferError",
    "AccountNotOperableError",
    "AccountOwnershipError",
    "UnbalancedTransferError",
    "EntryAccountMismatchError",
    "InvalidAccountPurposeError",
    "AccountNotEmptyError",
    "AccountNotClosableError",
    "InvalidIdempotencyKeyError",
    "EntryDirectionMismatchError",
    "MalformedTransferError",
    "NaiveTimestampError",
    "ReversalMismatchError",
)


class TestDomainErrorTaxonomy:
    @pytest.mark.parametrize("error_name", NEW_ERROR_NAMES)
    def test_every_new_error_is_a_domain_error(self, error_name: str) -> None:
        error_class = getattr(account_balance_errors, error_name)
        assert issubclass(error_class, DomainError)

    def test_currency_mismatch_error_is_imported_from_shared_not_redefined(self) -> None:
        """The spec table lists CurrencyMismatchError, but it already exists in
        `shared.domain.errors` (I4). Redefining it here would create two distinct types with the
        same name, which is worse than not having it."""
        assert account_balance_errors.CurrencyMismatchError is CurrencyMismatchError
