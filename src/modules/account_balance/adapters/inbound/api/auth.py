from uuid import UUID

from fastapi import Header, HTTPException, status

from modules.account_balance.domain.identifiers import OwnerId


async def resolve_caller_id(
    x_caller_id: str | None = Header(default=None, alias="X-Caller-Id"),
) -> OwnerId:
    """T2: resolves the caller from the simulated `X-Caller-Id` header.

    Missing or malformed (not a UUID) -> 401, before any other request
    validation runs. Declared with `default=None` rather than a required
    `Header(...)`, on purpose: FastAPI would otherwise reject a genuinely
    missing header itself, as a 422 validation error, before this function
    ever runs -- and the spec requires 401 for exactly that case.

    This is the whole authentication mechanism v1 ships (PRD §9.1's own
    allowance for a simulated adapter) -- no token, no signature, no expiry.
    """
    if x_caller_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="missing X-Caller-Id header"
        )
    try:
        return OwnerId(UUID(x_caller_id))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="malformed X-Caller-Id header"
        ) from exc
