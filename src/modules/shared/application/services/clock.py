from datetime import UTC, datetime


class Clock:
    """Single source of time for every entity in the service.

    Mirrors `IdGenerator`'s shape exactly (`next_id()` <-> `now()`): a plain
    class with one method, supplied to whatever needs it rather than read
    ambiently. The domain takes no ambient time (design D6) -- `Entry.occurred_at`
    and `Transfer.occurred_at` are both caller-supplied and both reject a naive
    datetime (`NaiveTimestampError`), so this is the one place in the
    application layer allowed to call `datetime.now()`, and it always does so
    in UTC.

    There is deliberately no alternative constructor: a local-time or naive
    datetime minted at any call site would push a timezone bug into the
    ledger's audit trail.
    """

    def now(self) -> datetime:
        return datetime.now(UTC)
