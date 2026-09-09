from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, status

from modules.account_balance.adapters.config.dependencies import AccountBalanceContainer
from modules.account_balance.adapters.inbound.api.auth import resolve_caller_id
from modules.account_balance.adapters.inbound.api.dtos import CollectionsReportResponseDto
from modules.account_balance.application.gateways.authorization_gateway import (
    UnauthorizedPrincipalError,
)
from modules.account_balance.application.use_cases.get_collections_report import (
    GetCollectionsReport,
)
from modules.account_balance.domain.identifiers import OwnerId, PrincipalId
from modules.shared.application.errors import ApplicationError
from modules.shared.domain.errors import DomainError

router = APIRouter(prefix="/collections", tags=["collections"])


@router.get("", response_model=CollectionsReportResponseDto)
@inject
async def get_collections_report(
    caller_id: OwnerId = Depends(resolve_caller_id),
    use_case: GetCollectionsReport = Depends(
        Provide[AccountBalanceContainer.get_collections_report]
    ),
) -> CollectionsReportResponseDto:
    """`GET /collections` (PRD §11.3) -- every `USER` account currently in negative balance, the
    credit exposure it represents, and how long each has been open.

    A singular resource, not `/accounts/negative` or a query flag on `GET /accounts`: this is not
    a filtered view of "my accounts" (it is not scoped to the caller's accounts at all, unlike
    every other `GET` in this module) -- it is the operator's own report, so it earns its own
    top-level path, the same way `/transfers/{id}/reversals` earns its own resource rather than
    being a flag on `POST /transfers`.

    Operator-only, same mechanism as reversal (R1): a collections list is, by construction, an
    enumeration of other customers' debts, so this reuses `AuthorizationGateway` /
    `FixedAdminAuthorizationGateway` rather than inventing a second authorization concept. `T2`'s
    `X-Caller-Id` is reused unchanged for identity resolution -- only its *type* narrows, from
    `OwnerId` to `PrincipalId`, exactly as `create_reversal` does in `transfer_routes.py`.
    """
    try:
        report = await use_case.execute(caller_id=PrincipalId(caller_id.value))
    except UnauthorizedPrincipalError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except DomainError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ApplicationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return CollectionsReportResponseDto.from_report(report)
