from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Header, HTTPException, status

from modules.account_balance.adapters.config.dependencies import AccountBalanceContainer
from modules.account_balance.adapters.inbound.api.auth import resolve_caller_id
from modules.account_balance.adapters.inbound.api.dtos import (
    DepositRequestDto,
    TransferResponseDto,
    WithdrawalRequestDto,
)
from modules.account_balance.application.gateways.account_repository import AccountNotFoundError
from modules.account_balance.application.services.system_account_resolver import (
    CurrencyNotOperationalError,
)
from modules.account_balance.application.use_cases.deposit import Deposit, DepositRequest
from modules.account_balance.application.use_cases.transfer_money import (
    IdempotencyConflictError,
    SystemToSystemTransferNotAllowedError,
)
from modules.account_balance.application.use_cases.withdraw import Withdraw, WithdrawRequest
from modules.account_balance.domain.errors import (
    AccountNotOperableError,
    AccountOwnershipError,
    InsufficientFundsError,
    InvalidIdempotencyKeyError,
    NonPositiveAmountError,
    SelfTransferError,
)
from modules.account_balance.domain.identifiers import AccountId, IdempotencyKey, OwnerId
from modules.shared.application.errors import ApplicationError
from modules.shared.domain.errors import CurrencyMismatchError, DomainError, InvalidCurrencyError
from modules.shared.domain.money import Currency, Money

router = APIRouter(tags=["deposits & withdrawals"])

# Same error-mapping table as transfer_routes.py -- a deposit or withdrawal
# *is* a Transfer (spec Purpose) and reuses TransferMoney's errors unchanged
# -- plus CurrencyNotOperationalError, which only these two endpoints can
# ever raise (openspec/specs/transfer/spec.md, "The design, settled").
_UNPROCESSABLE_ERRORS = (
    InsufficientFundsError,
    AccountNotOperableError,
    SelfTransferError,
    CurrencyMismatchError,
    NonPositiveAmountError,
    InvalidCurrencyError,
    InvalidIdempotencyKeyError,
)
_FORBIDDEN_ERRORS = (AccountOwnershipError, SystemToSystemTransferNotAllowedError)


@router.post("/deposits", response_model=TransferResponseDto, status_code=status.HTTP_201_CREATED)
@inject
async def create_deposit(
    payload: DepositRequestDto,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    caller_id: OwnerId = Depends(resolve_caller_id),
    use_case: Deposit = Depends(Provide[AccountBalanceContainer.deposit]),
) -> TransferResponseDto:
    try:
        request = DepositRequest(
            destination_account_id=AccountId(payload.destination_account_id),
            amount=Money(payload.amount, Currency(payload.currency)),
            idempotency_key=IdempotencyKey(idempotency_key),
            requested_by=caller_id,
        )
        transfer = await use_case.execute(request)
    except _FORBIDDEN_ERRORS as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except _UNPROCESSABLE_ERRORS as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except IdempotencyConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except CurrencyNotOperationalError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except AccountNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DomainError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ApplicationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return TransferResponseDto.from_transfer(transfer)


@router.post(
    "/withdrawals", response_model=TransferResponseDto, status_code=status.HTTP_201_CREATED
)
@inject
async def create_withdrawal(
    payload: WithdrawalRequestDto,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    caller_id: OwnerId = Depends(resolve_caller_id),
    use_case: Withdraw = Depends(Provide[AccountBalanceContainer.withdraw]),
) -> TransferResponseDto:
    try:
        request = WithdrawRequest(
            source_account_id=AccountId(payload.source_account_id),
            amount=Money(payload.amount, Currency(payload.currency)),
            idempotency_key=IdempotencyKey(idempotency_key),
            requested_by=caller_id,
        )
        transfer = await use_case.execute(request)
    except _FORBIDDEN_ERRORS as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except _UNPROCESSABLE_ERRORS as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except IdempotencyConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except CurrencyNotOperationalError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except AccountNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DomainError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ApplicationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return TransferResponseDto.from_transfer(transfer)
