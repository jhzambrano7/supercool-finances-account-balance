from decimal import Decimal

import pytest

from modules.shared.domain.errors import (
    CurrencyMismatchError,
    InvalidAmountError,
    InvalidCurrencyError,
)
from modules.shared.domain.money import Currency, Money

USD = Currency("USD")
EUR = Currency("EUR")


class TestCurrency:
    def test_accepts_an_iso_4217_code(self) -> None:
        assert Currency("USD").code == "USD"

    @pytest.mark.parametrize("code", ["usd", "US", "USDD", "US1", "", "  "])
    def test_rejects_anything_that_is_not_a_three_letter_code(self, code: str) -> None:
        with pytest.raises(InvalidCurrencyError):
            Currency(code)

    def test_equality_is_by_value(self) -> None:
        assert Currency("USD") == Currency("USD")
        assert Currency("USD") != Currency("EUR")


class TestMoneyConstruction:
    def test_holds_an_integer_amount_of_minor_units(self) -> None:
        assert Money(1_050, USD).amount == 1_050

    def test_may_be_negative(self) -> None:
        """SYSTEM accounts hold negative balances by design (PRD 4.1)."""
        assert Money(-500, USD).is_negative

    @pytest.mark.parametrize("amount", [10.5, 10.0, Decimal("10.50")])
    def test_rejects_non_integer_amounts(self, amount: object) -> None:
        """No float ever reaches the money path, not even one that looks exact."""
        with pytest.raises(InvalidAmountError):
            Money(amount, USD)  # type: ignore[arg-type]

    def test_rejects_bool_because_it_is_secretly_an_int(self) -> None:
        with pytest.raises(InvalidAmountError):
            Money(True, USD)  # type: ignore[arg-type]

    def test_rejects_a_bare_currency_code(self) -> None:
        with pytest.raises(InvalidCurrencyError):
            Money(100, "USD")  # type: ignore[arg-type]

    def test_zero_factory(self) -> None:
        assert Money.zero(USD) == Money(0, USD)

    def test_is_immutable(self) -> None:
        with pytest.raises(AttributeError):
            Money(100, USD).amount = 200  # type: ignore[misc]

    def test_is_hashable_by_value(self) -> None:
        assert len({Money(100, USD), Money(100, USD), Money(100, EUR)}) == 2


class TestMoneyArithmetic:
    def test_adds_within_a_currency(self) -> None:
        assert Money(100, USD) + Money(50, USD) == Money(150, USD)

    def test_subtracts_within_a_currency(self) -> None:
        assert Money(100, USD) - Money(150, USD) == Money(-50, USD)

    def test_negates(self) -> None:
        assert -Money(100, USD) == Money(-100, USD)

    def test_absolute_value(self) -> None:
        assert abs(Money(-100, USD)) == Money(100, USD)

    def test_arithmetic_is_exact_over_many_operations(self) -> None:
        """The reason integers are non-negotiable: 0.1 + 0.2 != 0.3 in floats."""
        total = Money.zero(USD)
        for _ in range(10):
            total += Money(10, USD)

        assert total == Money(100, USD)

    @pytest.mark.parametrize(
        "operation",
        [
            lambda a, b: a + b,
            lambda a, b: a - b,
        ],
    )
    def test_refuses_to_mix_currencies(self, operation: object) -> None:
        with pytest.raises(CurrencyMismatchError):
            operation(Money(100, USD), Money(100, EUR))  # type: ignore[operator]


class TestMoneyComparison:
    def test_orders_within_a_currency(self) -> None:
        assert Money(100, USD) < Money(200, USD)
        assert Money(200, USD) > Money(100, USD)
        assert Money(100, USD) <= Money(100, USD)
        assert Money(100, USD) >= Money(100, USD)

    @pytest.mark.parametrize(
        "operation",
        [
            lambda a, b: a < b,
            lambda a, b: a <= b,
            lambda a, b: a > b,
            lambda a, b: a >= b,
        ],
    )
    def test_refuses_to_order_across_currencies(self, operation: object) -> None:
        """100 JPY < 5 USD is not a question this type is willing to answer."""
        with pytest.raises(CurrencyMismatchError):
            operation(Money(100, USD), Money(100, EUR))  # type: ignore[operator]

    def test_equality_across_currencies_is_false_not_an_error(self) -> None:
        """Equality must stay total: it is used by containers and assertions."""
        assert Money(100, USD) != Money(100, EUR)

    def test_predicates(self) -> None:
        assert Money.zero(USD).is_zero
        assert Money(1, USD).is_positive
        assert Money(-1, USD).is_negative
