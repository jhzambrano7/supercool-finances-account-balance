from datetime import UTC, datetime

from modules.shared.application.services.clock import Clock


def test_now_is_timezone_aware() -> None:
    """The domain rejects a naive datetime (`NaiveTimestampError`) -- `Clock`
    must never be able to hand one out.
    """
    assert Clock().now().tzinfo is not None


def test_now_is_utc() -> None:
    assert Clock().now().utcoffset() == UTC.utcoffset(datetime.now(UTC))


def test_now_advances() -> None:
    clock = Clock()
    first = clock.now()
    second = clock.now()

    assert second >= first
