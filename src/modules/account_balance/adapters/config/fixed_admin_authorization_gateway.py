from logging import Logger

from modules.account_balance.adapters.config.admin_principal import ADMIN_PRINCIPAL_ID
from modules.account_balance.application.gateways.authorization_gateway import (
    AuthorizationGateway,
    UnauthorizedPrincipalError,
)
from modules.account_balance.domain.identifiers import PrincipalId


class FixedAdminAuthorizationGateway(AuthorizationGateway):
    """Simulated authorization (R1): exactly one hardcoded admin principal is authorized,
    documented in README.md -- mirrors how `seeded_accounts.py` hardcodes the platform's SYSTEM
    account ids rather than looking them up.
    """

    def __init__(self, *, logger: Logger) -> None:
        self._logger = logger

    async def authorize(self, principal_id: PrincipalId) -> None:
        if principal_id != PrincipalId(ADMIN_PRINCIPAL_ID):
            # Log before raising (docs/coding-conventions.md, application layer only): a rejected
            # reversal attempt is security-relevant, matching how every other expected-ish
            # rejection in this module (IdempotencyConflictError, CurrencyNotOperationalError)
            # logs first.
            self._logger.warning(
                "principal %s is not authorized to perform this operation", principal_id
            )
            raise UnauthorizedPrincipalError(principal_id=principal_id)
