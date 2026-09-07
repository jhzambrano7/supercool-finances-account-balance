from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Header, HTTPException, status

from modules.account_balance.adapters.config.dependencies import AccountBalanceContainer
from modules.account_balance.adapters.inbound.api.auth import resolve_caller_id
from modules.account_balance.adapters.inbound.api.transfer_schemas import (
    TransferRequest,
    TransferResponse,
)
from modules.account_balance.application.use_cases.revert_transfer import (
    RevertTransfer,
    RevertTransferUseCase,
    TransferAlreadyReversedError,
    TransferNotFoundError,
)
from modules.account_balance.application.use_cases.transfer_money import (
    AccountNotFoundError,
    IdempotencyConflictError,
    SystemToSystemTransferNotAllowedError,
    TransferMoney,
    TransferMoneyUseCase,
)
from modules.account_balance.domain.errors import (
    AccountNotOperableError,
    AccountOwnershipError,
    InsufficientFundsError,
    InvalidIdempotencyKeyError,
    NonPositiveAmountError,
    SelfTransferError,
)
from modules.account_balance.domain.identifiers import (
    AccountId,
    IdempotencyKey,
    OwnerId,
    TransferId,
)
from modules.shared.domain.errors import CurrencyMismatchError, DomainError, InvalidCurrencyError
from modules.shared.domain.money import Currency, Money

router = APIRouter(prefix="/transfers", tags=["transfers"])

# Error-mapping table (openspec/specs/transfer/spec.md, "Domain Errors Map To
# Stable HTTP Statuses"). NonPositiveAmountError, InvalidCurrencyError and
# InvalidIdempotencyKeyError are all reachable straight from this endpoint's
# own input (a zero/negative amount, a currency code that fails Currency's
# ISO-4217 *shape* check, a blank or over-length Idempotency-Key header) --
# ordinary client mistakes, not the "should never happen" class the 500
# default below exists for.
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


@router.post("", response_model=TransferResponse, status_code=status.HTTP_201_CREATED)
@inject
async def create_transfer(
    payload: TransferRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    caller_id: OwnerId = Depends(resolve_caller_id),
    use_case: TransferMoneyUseCase = Depends(
        Provide[AccountBalanceContainer.transfer_money_use_case]
    ),
) -> TransferResponse:
    try:
        request = TransferMoney(
            source_account_id=AccountId(payload.source_account_id),
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
    except AccountNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DomainError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="unexpected domain error"
        ) from exc

    # T4: an idempotent replay returns the same 201 status as the original
    # creation -- there is no distinct "replay" status, unlike AO3's 200/201
    # split for account-opening (spec T4).
    return TransferResponse.from_transfer(transfer)


# Error-mapping table (openspec/specs/revert/spec.md, "Domain And Use-Case
# Errors Map To Stable HTTP Statuses"). Deliberately narrow (R8):
# `AccountOwnershipError` and the amount/currency validation errors are
# unreachable from this endpoint's own input by construction (R1, R2, R5)
# and are not mapped here -- any `DomainError` this route did not
# anticipate falls to the defensive 500 below, on purpose, rather than
# being silently added to a 422/403 bucket that would misrepresent what
# this endpoint actually validates.
#
# `InvalidIdempotencyKeyError` is the one exception: the spec's own R8 text
# lists it alongside the amount/currency errors as "unreachable by
# construction (R1, R2, R5)", but none of those decisions touch idempotency
# key *validity* -- it is still constructed straight from client input
# here (the `Idempotency-Key` header), the same as `create_transfer` above,
# so a blank or over-length key reaches `IdempotencyKey.__post_init__`
# exactly as it does there. Mapped to 422 to avoid reproducing the very bug
# `transfer`'s own error table already found and fixed once (a
# `DomainError` reachable from this endpoint's own input falling through to
# the 500 default). See the final report for this finding.
_UNPROCESSABLE_ERRORS_REVERT = (InvalidIdempotencyKeyError,)


@router.post(
    "/{transfer_id}/reversals", response_model=TransferResponse, status_code=status.HTTP_201_CREATED
)
@inject
async def create_reversal(
    transfer_id: UUID,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    caller_id: OwnerId = Depends(resolve_caller_id),
    use_case: RevertTransferUseCase = Depends(
        Provide[AccountBalanceContainer.revert_transfer_use_case]
    ),
) -> TransferResponse:
    try:
        request = RevertTransfer(
            transfer_id=TransferId(transfer_id),
            idempotency_key=IdempotencyKey(idempotency_key),
            requested_by=caller_id,
        )
        reversal = await use_case.execute(request)
    except _UNPROCESSABLE_ERRORS_REVERT as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except (TransferAlreadyReversedError, IdempotencyConflictError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (TransferNotFoundError, AccountNotFoundError) as exc:
        # AccountNotFoundError here is defensive only (revert_transfer.py's
        # own pragma: no cover): both accounts are FK-backed by the original
        # transfer's own rows, so this cannot happen through the API today.
        # Caught anyway, not left to bypass the DomainError net below, for
        # the same reason create_transfer above catches it explicitly.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DomainError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="unexpected domain error"
        ) from exc

    # T4/R3: an idempotent replay returns the same 201 status as the
    # original creation, same as `create_transfer` above.
    return TransferResponse.from_transfer(reversal)
