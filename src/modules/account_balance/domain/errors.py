from modules.shared.domain.errors import CurrencyMismatchError, DomainError

# Re-exported so every account-balance module can import its whole error
# vocabulary from one place, including the one member that already existed in
# `shared` (I4). Nothing else from `shared.domain.errors` is re-exported here:
# this module speaks only the account-balance vocabulary.
__all__ = [
    "AccountNotClosableError",
    "AccountNotEmptyError",
    "AccountNotOperableError",
    "AccountOwnershipError",
    "CurrencyMismatchError",
    "EntryAccountMismatchError",
    "EntryDirectionMismatchError",
    "InsufficientFundsError",
    "InvalidAccountPurposeError",
    "InvalidIdempotencyKeyError",
    "MalformedTransferError",
    "NaiveTimestampError",
    "NonPositiveAmountError",
    "ReversalMismatchError",
    "SelfTransferError",
    "UnbalancedTransferError",
]


class InsufficientFundsError(DomainError):
    """A debit would take a USER account below zero (I2).

    `debit_for_reversal` is the sole path exempt from this check — see design
    §4.2 and §4.3's architecture test.
    """


class NonPositiveAmountError(DomainError):
    """A transfer or entry amount is <= 0 (I3).

    A zero or negative movement is not a smaller transfer — it is not a
    transfer at all, so it is rejected rather than accepted as a no-op.
    """


class SelfTransferError(DomainError):
    """Source and destination are the same account (I5).

    A transfer that starts and ends at the same place explains nothing about
    where money moved, so it is refused rather than accepted as a zero-effect
    operation.
    """


class AccountNotOperableError(DomainError):
    """The account's status forbids the requested debit or credit.

    Raised by `credit()`, `debit()` and `debit_for_reversal()` when the
    account is not `ACTIVE`. Distinct from `AccountNotClosableError`: a
    caller can act on this one (the account simply cannot move money right
    now), whereas "why can't I close it" is a different question entirely.
    """


class AccountOwnershipError(DomainError):
    """The requester does not own the account being asserted (G5).

    The domain only states this fact; deciding which legs of a movement must
    pass this check is use-case policy (design §5.3), not a domain rule.
    """


class UnbalancedTransferError(DomainError):
    """I1 would be violated at construction.

    Internal guard: `Transfer.__post_init__` computes this from the legs it
    was given, so if this ever fires in production it is a bug in the code
    that assembled the legs, not a customer error.
    """


class EntryAccountMismatchError(DomainError):
    """An entry meant for one account was applied to another.

    Internal guard inside `Account._validated_balance` — the caller wired an
    entry to the wrong account, which is a programming error, not a business
    rule a customer can trigger.
    """


class InvalidAccountPurposeError(DomainError):
    """`purpose` does not belong to `account_type` on `Account.open()` (§4.4).

    Named `InvalidAccountPurposeError` rather than the design's working
    `AccountPurposeMismatchError`: a naming decision inside an existing
    convention, not a product decision, resolved in the spec's "Resolved
    after the first draft" section.
    """


class AccountNotEmptyError(DomainError):
    """`Account.close()` was called on a non-zero balance (§7.2).

    Split from `AccountNotClosableError` because this one is actionable —
    empty the account and try again — and a caller would handle the two
    causes differently.
    """


class AccountNotClosableError(DomainError):
    """`Account.close()` was called on an account that cannot ever close.

    Covers "not ACTIVE" and "SYSTEM-typed" (§7.2). Neither cause is
    actionable by a customer and nothing branches differently between them,
    so they share this one type; the message carries the specific reason.
    """


class InvalidIdempotencyKeyError(DomainError):
    """The idempotency key is empty (after stripping) or exceeds 255 characters.

    No format is imposed beyond the bound: the key is client-generated and a
    UUID, a ULID and a request hash are all legitimate (§6.3). The bound
    exists because an unbounded client-supplied string is a storage and abuse
    concern, not because any particular length is meaningful.
    """


class EntryDirectionMismatchError(DomainError):
    """A leg was applied through the method that does not match its direction.

    Internal guard: `credit()` rejects a `DEBIT` leg, and `debit()` /
    `debit_for_reversal()` reject a `CREDIT` leg. The sign is chosen once, on
    the entry (D2) — a method that accepted either direction would be a
    second place the sign is decided.
    """


class MalformedTransferError(DomainError):
    """A transfer's legs do not describe the transfer they belong to.

    Internal guard: fewer than two legs, an entry whose `transfer_id` does
    not match, or neither the debited nor the credited legs represent the
    stated source/destination. A balance is explained by its entries —
    entries that describe a different movement explain nothing.
    """


class NaiveTimestampError(DomainError):
    """`occurred_at` carries no timezone.

    The entry trail is the audit record; "14:30" is not a time until you
    know where, and a ledger ordered by ambiguous timestamps cannot be
    reconciled across a daylight-saving boundary.
    """


class ReversalMismatchError(DomainError):
    """A reversal's legs do not mirror the transfer it reverses.

    Internal guard: the accounts passed to `revert()` must be exactly the
    original transfer's destination (now debited) and source (now credited).
    A compensating entry that does not compensate is just another transfer
    wearing a reference to one.
    """
