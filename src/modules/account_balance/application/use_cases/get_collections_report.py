from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from logging import Logger

from modules.account_balance.application.gateways.authorization_gateway import (
    AuthorizationGateway,
)
from modules.account_balance.application.gateways.collections_repository import (
    CollectionsRepository,
)
from modules.account_balance.application.gateways.models.negative_balance import (
    NEGATIVE_AGE_BUCKET_ORDER,
    CollectionsReport,
    CurrencyExposure,
    NegativeAgeBucket,
    NegativeBalanceAccount,
)
from modules.account_balance.domain.identifiers import PrincipalId
from modules.shared.application.services.clock import Clock
from modules.shared.domain.money import Currency, Money

# Ascending, paired with each bucket's *upper* bound -- `None` for the open-ended last bucket.
# Kept next to the use case that interprets it, not in the model module: the model only names the
# buckets that exist (NEGATIVE_AGE_BUCKET_ORDER), this decides where their edges fall.
_BUCKET_CEILINGS: tuple[tuple[NegativeAgeBucket, timedelta | None], ...] = (
    (NegativeAgeBucket.UNDER_ONE_DAY, timedelta(days=1)),
    (NegativeAgeBucket.ONE_TO_SEVEN_DAYS, timedelta(days=7)),
    (NegativeAgeBucket.SEVEN_TO_THIRTY_DAYS, timedelta(days=30)),
    (NegativeAgeBucket.OVER_THIRTY_DAYS, None),
)


class GetCollectionsReport:
    """Operator-only read of every negative `USER` balance, the exposure it represents, and how
    long each has been open (PRD §11.3).

    Authorization reuses R1's exact machinery (`AuthorizationGateway`) rather than inventing a
    second authorization concept: a collections list is, by construction, an enumeration of other
    customers' debts, and the reasoning that makes reversal operator-only (PRD §7.1 -- the
    authority to touch other people's money/records sits with an operator) applies here with equal
    force. Unlike `GetAccount`/`ListMovements`, there is no per-account ownership check to make
    instead -- this endpoint is not scoped to the caller's own accounts at all, so
    `AuthorizationGateway` is the *only* gate, exactly as it is for `RevertTransfer`.
    """

    def __init__(
        self,
        *,
        collections_repository: CollectionsRepository,
        authorization_gateway: AuthorizationGateway,
        clock: Clock,
        logger: Logger,
    ) -> None:
        self._collections_repository = collections_repository
        self._authorization_gateway = authorization_gateway
        self._clock = clock
        self._logger = logger

    async def execute(self, *, caller_id: PrincipalId) -> CollectionsReport:
        # Checked first and only, mirroring RevertTransfer.execute(): a stateless gate over who is
        # calling, not a fact any race could invalidate -- `FixedAdminAuthorizationGateway` itself
        # logs the rejection (docs/coding-conventions.md: log before raising, application layer
        # only), so there is nothing to add here beyond letting it propagate.
        await self._authorization_gateway.authorize(caller_id)

        accounts = await self._collections_repository.find_negative_balances()
        # One clock read for the whole report (see CollectionsReport's own docstring) -- every
        # row's age is computed against this same instant, not against whenever its own row
        # happened to be processed.
        as_of = self._clock.now()
        ordered = tuple(sorted(accounts, key=_sort_key))

        unexplained = sum(1 for account in ordered if account.negative_since is None)
        if unexplained:
            # A drifted balance is a data-integrity fact (PRD §11.1), not routine collections
            # traffic -- worth its own log line, distinct from the ordinary count below, so an
            # operator scanning logs does not have to open the response body to notice it.
            self._logger.warning(
                "collections report: %d account(s) negative with no explaining entry history "
                "(materialized-balance drift, PRD §11.1)",
                unexplained,
            )
        self._logger.info("collections report: %d account(s) currently negative", len(ordered))

        return CollectionsReport(
            as_of=as_of,
            accounts=ordered,
            exposures=_exposures_by_currency(ordered),
            age_buckets=_age_buckets(ordered, as_of),
        )


# The oldest datetime `datetime.min` can represent, tz-aware to compare against
# `negative_since`'s own tz-aware values (every `Entry.occurred_at` is tz-aware by construction,
# `NaiveTimestampError`) -- used only as an ordering key, never returned or stored.
_UNEXPLAINED_SORT_FLOOR = datetime.min.replace(tzinfo=UTC)


def _sort_key(account: NegativeBalanceAccount) -> tuple[int, datetime]:
    """Unexplained accounts (`negative_since is None`, PRD §11.1's balance drift) sort first,
    ahead of every dated row regardless of age: a data-integrity question outranks even the
    oldest ordinary collections case. `None` cannot be compared to a `datetime` directly (`sorted`
    would raise `TypeError`), so the tuple's first element does the partitioning and the second
    is only ever compared within the same partition.
    """
    if account.negative_since is None:
        return (0, _UNEXPLAINED_SORT_FLOOR)
    return (1, account.negative_since)


def _exposures_by_currency(
    accounts: tuple[NegativeBalanceAccount, ...],
) -> tuple[CurrencyExposure, ...]:
    counts: dict[Currency, int] = {}
    totals: dict[Currency, Money] = {}
    for account in accounts:
        currency = account.balance.currency
        counts[currency] = counts.get(currency, 0) + 1
        # balance is negative here by construction (this port never returns anything else) --
        # negated so "owed" reads as a positive amount.
        owed = -account.balance
        totals[currency] = totals.get(currency, Money.zero(currency)) + owed
    # Sorted by code for a deterministic response -- dict insertion order would otherwise depend
    # on which currency's first negative account the repository happened to return first.
    return tuple(
        CurrencyExposure(currency=currency, count=counts[currency], total_owed=totals[currency])
        for currency in sorted(totals, key=lambda currency: currency.code)
    )


def _age_buckets(
    accounts: tuple[NegativeBalanceAccount, ...], as_of: datetime
) -> Mapping[NegativeAgeBucket, int]:
    counts: dict[NegativeAgeBucket, int] = dict.fromkeys(NEGATIVE_AGE_BUCKET_ORDER, 0)
    for account in accounts:
        if account.negative_since is None:
            # No known start, so no defensible age -- excluded from every bucket rather than
            # guessed into one. `CollectionsReport.unexplained_count` is where this account is
            # counted instead.
            continue
        age = as_of - account.negative_since
        for bucket, ceiling in _BUCKET_CEILINGS:
            if ceiling is None or age < ceiling:
                counts[bucket] += 1
                break
    return counts
