from uuid import UUID, uuid4

import pytest

from modules.account_balance.domain.errors import InvalidIdempotencyKeyError
from modules.account_balance.domain.identifiers import (
    PLATFORM_OWNER_ID,
    AccountId,
    EntityId,
    IdempotencyKey,
    TransferId,
)


class TestEntityId:
    def test_rejects_a_non_uuid_value(self) -> None:
        with pytest.raises((TypeError, ValueError)):
            EntityId("not-a-uuid")  # type: ignore[arg-type]

    def test_platform_owner_id_is_the_nil_uuid(self) -> None:
        assert PLATFORM_OWNER_ID.value == UUID(int=0)


class TestTypedIdentifiersAreNotInterchangeable:
    def test_different_identifier_types_never_compare_equal(self) -> None:
        """Same UUID, different identifier types: must not compare equal (G1)."""
        u = uuid4()
        assert AccountId(u) != TransferId(u)


class TestAccountIdentifiersSupportDeterministicOrdering:
    def test_two_account_ids_are_orderable(self) -> None:
        """Exactly one of `<`/`>` holds, consistently on repeated comparison."""
        a = AccountId(uuid4())
        b = AccountId(uuid4())
        if a.value < b.value:
            assert a < b
            assert not (a > b)
            assert a < b  # repeated comparison agrees
        else:
            assert a > b
            assert not (a < b)
            assert a > b


class TestIdempotencyKey:
    def test_rejects_an_empty_string(self) -> None:
        with pytest.raises(InvalidIdempotencyKeyError):
            IdempotencyKey("")

    def test_rejects_a_whitespace_only_string(self) -> None:
        with pytest.raises(InvalidIdempotencyKeyError):
            IdempotencyKey("   ")

    def test_rejects_a_string_over_255_characters_after_stripping(self) -> None:
        with pytest.raises(InvalidIdempotencyKeyError):
            IdempotencyKey(" " + ("a" * 256) + " ")

    def test_accepts_exactly_255_characters(self) -> None:
        key = "a" * 255
        assert IdempotencyKey(key).value == key

    def test_strips_surrounding_whitespace(self) -> None:
        assert IdempotencyKey("  abc123  ").value == "abc123"
