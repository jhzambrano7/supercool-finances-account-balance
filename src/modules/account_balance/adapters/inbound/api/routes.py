from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Response, status

from modules.account_balance.adapters.config.dependencies import AccountBalanceContainer
from modules.account_balance.adapters.inbound.api.dtos import (
    AccountResponseDto,
    OpenAccountRequestDto,
)
from modules.account_balance.application.gateways.account_repository import (
    AccountAlreadyExistsError,
)
from modules.account_balance.application.use_cases.account_register import AccountRegister
from modules.account_balance.domain.errors import InvalidAccountPurposeError
from modules.account_balance.domain.identifiers import OwnerId
from modules.shared.domain.errors import DomainError, InvalidCurrencyError
from modules.shared.domain.money import Currency

router = APIRouter(prefix="/accounts", tags=["accounts"])

# Error-mapping table (openspec/specs/account-opening/spec.md, "Domain Errors
# Map To Stable HTTP Statuses"): only these two are reachable from this
# endpoint's own input; any other DomainError is a defensive 400 -- a domain
# error is a business-rule violation over the request/system state, not a
# system fault, so it is reported back to the caller rather than treated as
# an internal error.
_UNPROCESSABLE_ERRORS = (InvalidAccountPurposeError, InvalidCurrencyError)


@router.post("", response_model=AccountResponseDto, status_code=status.HTTP_201_CREATED)
@inject
async def open_account(
    payload: OpenAccountRequestDto,
    response: Response,
    use_case: AccountRegister = Depends(Provide[AccountBalanceContainer.account_register]),
) -> AccountResponseDto:
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
    except AccountAlreadyExistsError as exc:
        # AO4: the loser of a genuine concurrent race. The client's own
        # retry finds the account via the natural-key lookup the use case
        # already tries first, so this is reported, not recovered here.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except DomainError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    # AO3: 201 on first open, 200 when the natural key already existed. This
    # is an HTTP concept, so it is decided here, in the inbound adapter, not
    # asked of the application-layer result (docs/coding-conventions.md).
    response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    return AccountResponseDto.from_result(result)
