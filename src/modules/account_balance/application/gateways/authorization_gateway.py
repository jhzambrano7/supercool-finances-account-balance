from abc import ABC, abstractmethod

from modules.account_balance.domain.identifiers import PrincipalId
from modules.shared.application.errors import ApplicationError


class UnauthorizedPrincipalError(ApplicationError):
    """Raised when `principal_id` is not the platform's authorized admin (R1).

    Distinct from `AccountOwnershipError`: this is not a fact about which `Account` a caller owns,
    it is about whether the caller may invoke an operator-only operation at all -- a fact this
    port's adapter decides, not something any `Account` could answer.
    """

    def __init__(self, *, principal_id: PrincipalId) -> None:
        self.principal_id = principal_id
        super().__init__(f"principal {principal_id} is not authorized to perform this operation")


class AuthorizationGateway(ABC):
    """Port for verifying a principal may perform an operator-only operation (R1).

    Simulated in v1, the same allowance `X-Caller-Id` itself already relies on for authentication
    (T2; PRD §7.1: "which operator identities exist, and how they are proven, is the authentication
    adapter's problem") -- exactly one fixed admin principal is authorized, not a real
    identity-provider integration. `authorize` is async so a future real adapter (one that actually
    calls out to an identity provider) can replace this one without changing the port.
    """

    @abstractmethod
    async def authorize(self, principal_id: PrincipalId) -> None:
        """Raises `UnauthorizedPrincipalError` if `principal_id` is not the platform's admin."""
