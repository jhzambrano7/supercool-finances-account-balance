"""Unit tests for the movement cursor's own encode/decode round trip
(docs/web-ui-plan.md §6.2, "opaque to the client").
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from modules.account_balance.adapters.outbound.repositories.sql.queries.movement_cursor import (
    decode_movement_cursor,
    encode_movement_cursor,
)
from modules.account_balance.application.gateways.movement_repository import (
    InvalidMovementCursorError,
)


def test_encode_then_decode_round_trips() -> None:
    occurred_at = datetime(2026, 9, 7, 12, 34, 56, 789000, tzinfo=UTC)
    entry_id = uuid4()

    cursor = encode_movement_cursor(occurred_at, entry_id)
    decoded_occurred_at, decoded_entry_id = decode_movement_cursor(cursor)

    assert decoded_occurred_at == occurred_at
    assert decoded_entry_id == entry_id


@pytest.mark.parametrize("garbage", ["not-base64!!", "", "aGVsbG8=", "!!!"])
def test_a_cursor_this_module_did_not_issue_is_rejected(garbage: str) -> None:
    with pytest.raises(InvalidMovementCursorError) as exc_info:
        decode_movement_cursor(garbage)

    assert exc_info.value.cursor == garbage
