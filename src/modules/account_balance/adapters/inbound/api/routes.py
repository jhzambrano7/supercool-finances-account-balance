from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Response, status

from modules.account_balance.adapters.config.dependencies import AccountBalanceContainer
from modules.account_balance.adapters.inbound.api.schemas import (
    AccountResponse,
    OpenAccountRequest,
)
from modules.account_balance.application.use_cases.open_account import OpenAccountUseCase
from modules.account_balance.domain.errors import InvalidAccountPurposeError
from modules.account_balance.domain.identifiers import OwnerId
from modules.shared.domain.errors import DomainError, InvalidCurrencyError
from modules.shared.domain.money import Currency

router = APIRouter(prefix="/accounts", tags=["accounts"])

# Error-mapping table (openspec/specs/account-opening/spec.md, "Domain Errors
# Map To Stable HTTP Statuses"): only these two are reachable from this
# endpoint's own input; any other DomainError is a defensive 500.
_UNPROCESSABLE_ERRORS = (InvalidAccountPurposeError, InvalidCurrencyError)


@router.post("", response_model=AccountResponse, status_code=status.HTTP_201_CREATED)
@inject
async def open_account(
    payload: OpenAccountRequest,
    response: Response,
    use_case: OpenAccountUseCase = Depends(Provide[AccountBalanceContainer.open_account_use_case]),
) -> AccountResponse:
    try:
        currency = Currency(payload.currency)
        result = await use_case.execute(
            owner_id=OwnerId(payload.owner_id),
            purpose=payload.purpose,
            currency=currency,
        )
    except _UNPROCESSABLE_ERRORS as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except DomainError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="unexpected domain error"
        ) from exc

    # AO3: 201 on first open, 200 when the natural key already existed —
    # the use case's own result decides which (Tell, Don't Ask).
    response.status_code = result.http_status
    return AccountResponse.from_result(result)
