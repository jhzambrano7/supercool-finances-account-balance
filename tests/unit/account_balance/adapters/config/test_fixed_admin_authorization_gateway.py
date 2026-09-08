"""Unit tests for `FixedAdminAuthorizationGateway` -- the concrete adapter, not the fake that
stands in for it in `test_revert_transfer_use_case.py`. Confirms the real comparison against
`ADMIN_PRINCIPAL_ID` actually authorizes the admin and rejects everyone else."""

import logging
from uuid import uuid4

import pytest

from modules.account_balance.adapters.config.admin_principal import ADMIN_PRINCIPAL_ID
from modules.account_balance.adapters.config.fixed_admin_authorization_gateway import (
    FixedAdminAuthorizationGateway,
)
from modules.account_balance.application.gateways.authorization_gateway import (
    UnauthorizedPrincipalError,
)
from modules.account_balance.domain.identifiers import PrincipalId


async def test_the_admin_principal_is_authorized() -> None:
    gateway = FixedAdminAuthorizationGateway(logger=logging.getLogger(__name__))

    await gateway.authorize(PrincipalId(ADMIN_PRINCIPAL_ID))


async def test_any_other_principal_is_rejected() -> None:
    gateway = FixedAdminAuthorizationGateway(logger=logging.getLogger(__name__))

    with pytest.raises(UnauthorizedPrincipalError):
        await gateway.authorize(PrincipalId(uuid4()))
