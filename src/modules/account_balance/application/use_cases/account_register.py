from dataclasses import dataclass

from modules.account_balance.application.gateways.account_repository import AccountRepository
from modules.account_balance.application.gateways.models.find_accounts_criteria import (
    FindAccountByOwnerAndPurposeAndCurrency,
)
from modules.account_balance.domain.account import Account, AccountPurpose, UserAccount
from modules.account_balance.domain.identifiers import AccountId, OwnerId
from modules.shared.application.services.id_generator import IdGenerator
from modules.shared.domain.money import Currency


@dataclass(frozen=True, slots=True)
class OpenAccountResult:
    """What opening produced: the account, and whether this call is the one
    that created it (AO2, AO3) — a plain fact. Whether that fact means HTTP
    200 or 201 is an HTTP concept and belongs to the inbound adapter, not
    here — the application layer must not know FastAPI or any status code
    exists.
    """

    account: Account
    created: bool


class AccountRegister:
    """Opens a `USER` account, idempotent by natural key (AO1-AO4).

    Only `USER` accounts open through this door (AO1) — `account_type` is
    not a parameter, `SYSTEM` accounts are platform-seeded infrastructure.
    """

    def __init__(self, *, repository: AccountRepository, id_generator: IdGenerator) -> None:
        self._repository = repository
        self._id_generator = id_generator

    async def execute(
        self, *, owner_id: OwnerId, purpose: AccountPurpose, currency: Currency
    ) -> OpenAccountResult:
        natural_key = FindAccountByOwnerAndPurposeAndCurrency(
            owner_id=owner_id, purpose=purpose, currency=currency
        )
        existing = await self._repository.find(criteria=natural_key)
        if existing is not None:
            return OpenAccountResult(account=existing, created=False)

        # UserAccount.open() validates the purpose and raises
        # InvalidAccountPurposeError before any persistence is attempted —
        # no account is ever half-opened.
        account = UserAccount.open(
            account_id=AccountId(self._id_generator.next_id()),
            owner_id=owner_id,
            purpose=purpose,
            currency=currency,
        )

        # AO4: if a concurrent request wins the natural-key race between the
        # find() above and this add(), AccountAlreadyExistsError propagates
        # to the caller rather than being recovered here -- a client that
        # hits this retries and finds the account via the same find() above,
        # which by then succeeds. Not recovering it in this narrow window
        # keeps this method's only job "open, or say why not."
        await self._repository.add(account)

        return OpenAccountResult(account=account, created=True)
