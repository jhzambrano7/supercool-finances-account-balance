from modules.shared.application.services.id_generator import IdGenerator


def test_generates_uuid_version_7() -> None:
    assert IdGenerator().next_id().version == 7


def test_ids_are_unique() -> None:
    generator = IdGenerator()
    ids = [generator.next_id() for _ in range(1_000)]

    assert len(set(ids)) == len(ids)


def test_ids_increase_over_time() -> None:
    """The ordering property is the whole reason for choosing UUIDv7.

    Ids generated later must never sort before earlier ones, otherwise index
    locality on the write-heavy ledger tables is lost.
    """
    generator = IdGenerator()
    ids = [generator.next_id() for _ in range(1_000)]

    assert ids == sorted(ids)
