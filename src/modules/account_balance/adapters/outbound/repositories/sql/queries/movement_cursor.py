import base64
from datetime import datetime
from uuid import UUID

from modules.account_balance.application.gateways.movement_repository import (
    InvalidMovementCursorError,
)

_SEPARATOR = "|"


def encode_movement_cursor(occurred_at: datetime, entry_id: UUID) -> str:
    """The opaque cursor a caller passes back: `(occurred_at, entry_id)` descending, base64-encoded
    so its shape is not something a client could reasonably try to construct or interpret
    (docs/web-ui-plan.md §6.2, "opaque to the client"). `entry_id` breaks ties between two entries
    of the same transfer, which share an `occurred_at`.
    """
    raw = f"{occurred_at.isoformat()}{_SEPARATOR}{entry_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_movement_cursor(cursor: str) -> tuple[datetime, UUID]:
    """Raises `InvalidMovementCursorError` for anything this module did not itself encode --
    a cursor is opaque, so any decode failure means the caller sent a value this service never
    issued.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        occurred_at_str, entry_id_str = raw.split(_SEPARATOR, 1)
        return datetime.fromisoformat(occurred_at_str), UUID(entry_id_str)
    except Exception as exc:
        raise InvalidMovementCursorError(cursor) from exc
