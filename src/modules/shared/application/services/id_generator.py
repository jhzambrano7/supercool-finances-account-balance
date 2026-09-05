from uuid import UUID

from uuid6 import uuid7


class IdGenerator:
    """Single source of identity for every entity in the service.

    Always UUIDv7. Its leading timestamp keeps generated ids roughly ordered, so
    inserts land near the right edge of a B-tree index instead of scattering
    across it. That matters most for the append-only ledger tables, which are
    write-heavy by design, and costs nothing for the rest.

    There is deliberately no alternative: offering a random-id method would push
    an index-performance decision onto every call site, and it only has to be
    wrong once to degrade a table nobody is watching.
    """

    def next_id(self) -> UUID:
        return uuid7()
