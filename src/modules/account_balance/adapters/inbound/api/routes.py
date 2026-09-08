from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from modules.account_balance.adapters.config.dependencies import AccountBalanceContainer
from modules.account_balance.adapters.inbound.api.auth import resolve_caller_id
from modules.account_balance.adapters.inbound.api.dtos import (
    AccountResponseDto,
    AccountsResponseDto,
    MovementsResponseDto,
    OpenAccountRequestDto,
)
from modules.account_balance.application.gateways.account_repository import (
    AccountAlreadyExistsError,
    AccountNotFoundError,
)
from modules.account_balance.application.gateways.movement_repository import (
    InvalidMovementCursorError,
)
from modules.account_balance.application.use_cases.account_register import AccountRegister
from modules.account_balance.application.use_cases.get_account import GetAccount
from modules.account_balance.application.use_cases.list_accounts import ListAccounts
from modules.account_balance.application.use_cases.list_movements import ListMovements
from modules.account_balance.domain.errors import AccountOwnershipError, InvalidAccountPurposeError
from modules.account_balance.domain.identifiers import AccountId, OwnerId
from modules.shared.application.errors import ApplicationError
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
    except ApplicationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    # AO3: 201 on first open, 200 when the natural key already existed. This
    # is an HTTP concept, so it is decided here, in the inbound adapter, not
    # asked of the application-layer result (docs/coding-conventions.md).
    response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    return AccountResponseDto.from_result(result)


@router.get("", response_model=AccountsResponseDto)
@inject
async def list_accounts(
    caller_id: OwnerId = Depends(resolve_caller_id),
    use_case: ListAccounts = Depends(Provide[AccountBalanceContainer.list_accounts]),
) -> AccountsResponseDto:
    """`GET /accounts` (docs/web-ui-plan.md §6.1b) -- no query parameters, the owner is the
    caller. `resolve_caller_id` alone accounts for every error this route can raise (a missing or
    malformed `X-Caller-Id` -> 401); there is nothing else here for a caller to get wrong."""
    accounts = await use_case.execute(caller_id=caller_id)
    return AccountsResponseDto(items=[AccountResponseDto.from_account(a) for a in accounts])


@router.get("/{account_id}", response_model=AccountResponseDto)
@inject
async def get_account(
    account_id: UUID,
    caller_id: OwnerId = Depends(resolve_caller_id),
    use_case: GetAccount = Depends(Provide[AccountBalanceContainer.get_account]),
) -> AccountResponseDto:
    """`GET /accounts/{account_id}` (docs/web-ui-plan.md §6.1) -- stricter authorization than
    `POST /transfers`' (ownership, not either-leg membership), since this returns a balance."""
    try:
        account = await use_case.execute(account_id=AccountId(account_id), caller_id=caller_id)
    except AccountOwnershipError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except AccountNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DomainError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ApplicationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return AccountResponseDto.from_account(account)


@router.get("/{account_id}/movements", response_model=MovementsResponseDto)
@inject
async def list_movements(
    account_id: UUID,
    limit: int = Query(default=25, ge=1, le=100),
    cursor: str | None = Query(default=None),
    caller_id: OwnerId = Depends(resolve_caller_id),
    use_case: ListMovements = Depends(Provide[AccountBalanceContainer.list_movements]),
) -> MovementsResponseDto:
    """`GET /accounts/{account_id}/movements` (docs/web-ui-plan.md §6.2) -- cursor pagination,
    newest first; `cursor` is opaque, `limit` defaults to 25 and caps at 100."""
    try:
        page = await use_case.execute(
            account_id=AccountId(account_id), caller_id=caller_id, limit=limit, cursor=cursor
        )
    except InvalidMovementCursorError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except AccountOwnershipError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except AccountNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DomainError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ApplicationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return MovementsResponseDto.from_page(page)
