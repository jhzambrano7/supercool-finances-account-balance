from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Self

from modules.shared.domain.errors import (
    CurrencyMismatchError,
    InvalidAmountError,
    InvalidCurrencyError,
)

_ISO_4217 = re.compile(r"^[A-Z]{3}$")


@dataclass(frozen=True, slots=True)
class Currency:
    """An ISO 4217 alphabetic code.

    Deliberately does not carry the minor-unit exponent. Inside this service
    money only ever exists in minor units, so the exponent is needed solely to
    render or parse a human-facing decimal — a presentation concern that belongs
    to the inbound adapter, not to the domain.
    """

    code: str

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not _ISO_4217.match(self.code):
            raise InvalidCurrencyError(
                f"currency must be a three-letter ISO 4217 code, got {self.code!r}"
            )

    def __str__(self) -> str:
        return self.code


@dataclass(frozen=True, slots=True, eq=True)
class Money:
    """An exact amount of a single currency, held in minor units.

    Two rules define this type, and both exist because of how money breaks:

    Amounts are integers in the currency's minor unit (cents, not euros).
    Floating point cannot represent 0.1 exactly, so repeated arithmetic drifts;
    a ledger that drifts is a ledger that cannot be reconciled. There is no
    constructor accepting a float.

    Currency is part of the value, not metadata beside it. Combining or
    comparing different currencies raises instead of coercing, which makes an
    entire class of corruption unrepresentable rather than merely discouraged.

    Money may be negative: SYSTEM accounts hold negative balances by design (see
    docs/prd.md 4.1). Positivity is asserted where a rule demands it, such as a
    transfer amount, not by this type.
    """

    amount: int
    currency: Currency

    def __post_init__(self) -> None:
        # bool is a subclass of int, so Money(True, usd) would silently mean 1.
        if isinstance(self.amount, bool) or not isinstance(self.amount, int):
            raise InvalidAmountError(
                f"amount must be an integer number of minor units, got "
                f"{type(self.amount).__name__} {self.amount!r}"
            )
        if not isinstance(self.currency, Currency):
            raise InvalidCurrencyError(f"currency must be a Currency, got {self.currency!r}")

    @classmethod
    def zero(cls, currency: Currency) -> Self:
        return cls(0, currency)

    # -- arithmetic ---------------------------------------------------------
    def __add__(self, other: Money) -> Money:
        self._assert_same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._assert_same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __abs__(self) -> Money:
        return Money(abs(self.amount), self.currency)

    # -- ordering -----------------------------------------------------------
    # Implemented by hand rather than via `order=True`: the generated version
    # compares the (amount, currency) tuple, so 100 JPY < 5 USD would quietly
    # return a result instead of rejecting the question.
    def __lt__(self, other: Money) -> bool:
        self._assert_same_currency(other)
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        self._assert_same_currency(other)
        return self.amount <= other.amount

    def __gt__(self, other: Money) -> bool:
        self._assert_same_currency(other)
        return self.amount > other.amount

    def __ge__(self, other: Money) -> bool:
        self._assert_same_currency(other)
        return self.amount >= other.amount

    # -- predicates ---------------------------------------------------------
    @property
    def is_zero(self) -> bool:
        return self.amount == 0

    @property
    def is_positive(self) -> bool:
        return self.amount > 0

    @property
    def is_negative(self) -> bool:
        return self.amount < 0

    def _assert_same_currency(self, other: Money) -> None:
        if not isinstance(other, Money):
            raise CurrencyMismatchError(f"expected Money, got {type(other).__name__}")
        if self.currency != other.currency:
            raise CurrencyMismatchError(
                f"cannot operate on {self.currency} and {other.currency} without a rate"
            )

    def __str__(self) -> str:
        return f"{self.amount} {self.currency}"
