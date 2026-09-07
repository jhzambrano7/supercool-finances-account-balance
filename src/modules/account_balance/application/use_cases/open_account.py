from dataclasses import dataclass

from modules.account_balance.application.gateways.account_repository import (
    AccountAlreadyExistsError,
    AccountRepository,
)
from modules.account_balance.application.gateways.models.find_accounts_criteria import (
    FindAccountByOwnerAndPurposeAndCurrency,
)
from modules.account_balance.domain.account import Account, AccountPurpose, AccountType
from modules.account_balance.domain.identifiers import AccountId, OwnerId
from modules.shared.application.services.id_generator import IdGenerator
from modules.shared.domain.money import Currency


@dataclass(frozen=True, slots=True)
class OpenAccountResult:
    """What opening produced, and whether the caller should see 201 or 200 (AO2, AO3).

    `http_status` applies Tell, Don't Ask (docs/coding-conventions.md): the
    HTTP adapter asks this result for its status code instead of reading
    `.created` and re-deriving the 200/201 mapping itself at the call site.
    Plain `int`, not a `fastapi.status` constant — the application layer
    must not depend on the inbound HTTP framework.
    """

    account: Account
    created: bool

    @property
    def http_status(self) -> int:
        return 201 if self.created else 200


class OpenAccountUseCase:
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

        # Account.open() validates the (account_type, purpose) pair and
        # raises InvalidAccountPurposeError before any persistence is
        # attempted — no account is ever half-opened.
        account = Account.open(
            account_id=AccountId(self._id_generator.next_id()),
            owner_id=owner_id,
            account_type=AccountType.USER,
            purpose=purpose,
            currency=currency,
        )

        try:
            await self._repository.add(account)
        except AccountAlreadyExistsError:
            # AO4: the insert lost the race. The winner already committed,
            # so re-reading the natural key returns it rather than erroring.
            winner = await self._repository.find(criteria=natural_key)
            if winner is None:
                raise  # pragma: no cover — defensive: the DB just told us it exists
            return OpenAccountResult(account=winner, created=False)

        return OpenAccountResult(account=account, created=True)
