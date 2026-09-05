from uuid import UUID, uuid4

from uuid6 import uuid7


class IdGenerator:
    def sorted_uuid(self) -> UUID:
        return uuid7()

    def unsorted_uuid(self) -> UUID:
        return uuid4()
