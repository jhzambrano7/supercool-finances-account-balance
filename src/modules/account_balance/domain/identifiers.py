from dataclasses import dataclass
from typing import Final
from uuid import UUID

from modules.account_balance.domain.errors import InvalidIdempotencyKeyError

_IDEMPOTENCY_KEY_MAX_LENGTH = 255


@dataclass(frozen=True, slots=True)
class EntityId:
    """A typed wrapper over a UUID identity.

    Deliberately not a `NewType`: at runtime a `NewType` is the underlying
    type, so an `AccountId` and a `TransferId` built from the same UUID would
    compare equal and be interchangeable wherever a UUID is accepted. A
    dataclass compares `other.__class__ is self.__class__` first, so two
    identifiers of different subclasses never compare equal even when they
    wrap the same value — the concrete reason this exists instead.

    Does not validate the UUID version: minting is the application layer's
    policy (the `IdGenerator` uses UUIDv7), and the domain must also accept
    ids minted elsewhere — including the nil UUID reserved for
    `PLATFORM_OWNER_ID`.
    """

    value: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.value, UUID):
            raise TypeError(f"{type(self).__name__} requires a UUID, got {self.value!r}")

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True, order=True)
class AccountId(EntityId):
    """Orderable so a caller can lock accounts in a deterministic id sequence.

    `order=True` generates comparisons over the single inherited `value`
    field; `UUID` is itself totally ordered, so this ordering is total
    (PRD §5, step 3 — deadlock avoidance via deterministic lock ordering).
    """


@dataclass(frozen=True, slots=True)
class TransferId(EntityId):
    pass


@dataclass(frozen=True, slots=True)
class EntryId(EntityId):
    pass


@dataclass(frozen=True, slots=True)
class OwnerId(EntityId):
    pass


# The nil UUID, never issued by any `IdGenerator` (which mints UUIDv7), which
# is what makes it safe to reserve as the owner of `SYSTEM` accounts.
PLATFORM_OWNER_ID: Final = OwnerId(UUID(int=0))


@dataclass(frozen=True, slots=True)
class PrincipalId(OwnerId):
    """Identifies whoever is authorized to perform an operator-only operation (revert's R1),
    as distinct from `OwnerId`'s more common use identifying a customer.

    Subclasses `OwnerId` rather than `EntityId` directly, deliberately: this codebase already
    reuses `OwnerId` broadly for "whoever the resolved `X-Caller-Id` identifies"
    (`TransferMoneyRequest.requested_by` is not literally "the account's owner" either -- either
    leg's owner may request a transfer). Shared infrastructure that is generically about "the
    caller" (`IdempotencyRecord.caller_id: OwnerId`) accepts a `PrincipalId` without being
    loosened, since a `PrincipalId` IS an `OwnerId` here -- while a field that specifically wants
    "the authorized principal, not just any caller" (`RevertTransferRequest.executed_by`) can say
    so at the type level.
    """


@dataclass(frozen=True, slots=True)
class IdempotencyKey:
    """A client-supplied retry key, carried on `Transfer` (§6.3).

    No format is imposed — a UUID, a ULID and a request hash are all
    legitimate, because the key is client-generated. The bound exists
    because an unbounded client-supplied string is a storage and abuse
    concern, not because any particular length is meaningful.
    """

    value: str

    def __post_init__(self) -> None:
        stripped = self.value.strip()
        if not stripped:
            raise InvalidIdempotencyKeyError("idempotency key must not be empty or blank")
        if len(stripped) > _IDEMPOTENCY_KEY_MAX_LENGTH:
            raise InvalidIdempotencyKeyError(
                f"idempotency key must be at most {_IDEMPOTENCY_KEY_MAX_LENGTH} characters, "
                f"got {len(stripped)}"
            )
        object.__setattr__(self, "value", stripped)
