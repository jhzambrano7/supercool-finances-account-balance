class DomainError(Exception):
    """Base for every rule this domain refuses to break.

    Domain errors are not validation errors. A validation error says the caller
    sent something malformed; a domain error says the operation would have
    violated an invariant of the business, and would have been wrong even if
    every field parsed perfectly.
    """


class InvalidCurrencyError(DomainError):
    pass


class InvalidAmountError(DomainError):
    pass


class CurrencyMismatchError(DomainError):
    """Two amounts in different currencies were combined or compared.

    Never silently coerced: money in different currencies is not commensurable
    without an explicit rate, and guessing one is how balances get corrupted.
    """
