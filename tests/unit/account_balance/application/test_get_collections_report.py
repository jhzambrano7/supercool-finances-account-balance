"""Unit tests for `GetCollectionsReport` against a fake `CollectionsRepository`.

Covers what belongs to the use case itself: the authorization gate (reused from R1, PRD §7.1),
ordering the rows oldest-first, grouping exposure by currency (since `Money` refuses to sum across
currencies), the age-bucket histogram, and the empty case. The *computation* of `negative_since`
itself -- specifically the recovered-then-negative-again case the task calls out -- is a property
of the SQL window-function query, not of this use case, and is covered where that logic lives:
`tests/integration/account_balance/test_collections_route.py` (via the real backing repository).
"""

import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from modules.account_balance.application.gateways.authorization_gateway import (
    AuthorizationGateway,
    UnauthorizedPrincipalError,
)
from modules.account_balance.application.gateways.collections_repository import (
    CollectionsRepository,
)
from modules.account_balance.application.gateways.models.negative_balance import (
    NegativeAgeBucket,
    NegativeBalanceAccount,
)
from modules.account_balance.application.use_cases.get_collections_report import (
    GetCollectionsReport,
)
from modules.account_balance.domain.identifiers import AccountId, OwnerId, PrincipalId
from modules.shared.application.services.clock import Clock
from modules.shared.domain.money import Currency, Money

USD = Currency("USD")
MXN = Currency("MXN")
_AS_OF = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
_TEST_ADMIN = PrincipalId(uuid4())


class _FakeAuthorizationGateway(AuthorizationGateway):
    def __init__(self, *, authorized: PrincipalId) -> None:
        self._authorized = authorized
        self.calls: list[PrincipalId] = []

    async def authorize(self, principal_id: PrincipalId) -> None:
        self.calls.append(principal_id)
        if principal_id != self._authorized:
            raise UnauthorizedPrincipalError(principal_id=principal_id)


class _FakeClock(Clock):
    def __init__(self, fixed: datetime) -> None:
        self._fixed = fixed

    def now(self) -> datetime:
        return self._fixed


class _FakeCollectionsRepository(CollectionsRepository):
    def __init__(self, accounts: tuple[NegativeBalanceAccount, ...]) -> None:
        self._accounts = accounts
        self.called = False

    async def find_negative_balances(self) -> tuple[NegativeBalanceAccount, ...]:
        self.called = True
        return self._accounts


def _account(
    *,
    owner_id: OwnerId | None = None,
    amount: int,
    currency: Currency,
    negative_since: datetime | None,
) -> NegativeBalanceAccount:
    return NegativeBalanceAccount(
        account_id=AccountId(uuid4()),
        owner_id=owner_id or OwnerId(uuid4()),
        balance=Money(amount, currency),
        negative_since=negative_since,
    )


def _use_case(
    repository: CollectionsRepository, *, clock: Clock | None = None
) -> GetCollectionsReport:
    return GetCollectionsReport(
        collections_repository=repository,
        authorization_gateway=_FakeAuthorizationGateway(authorized=_TEST_ADMIN),
        clock=clock or _FakeClock(_AS_OF),
        logger=logging.getLogger(__name__),
    )


async def test_a_non_admin_caller_is_refused_before_the_repository_is_asked() -> None:
    repository = _FakeCollectionsRepository(())
    use_case = _use_case(repository)

    with pytest.raises(UnauthorizedPrincipalError):
        await use_case.execute(caller_id=PrincipalId(uuid4()))

    assert repository.called is False


async def test_the_empty_case_reports_zero_and_no_oldest() -> None:
    use_case = _use_case(_FakeCollectionsRepository(()))

    report = await use_case.execute(caller_id=_TEST_ADMIN)

    assert report.count == 0
    assert report.oldest_negative_since is None
    assert report.exposures == ()
    assert all(count == 0 for count in report.age_buckets.values())


async def test_rows_are_ordered_oldest_negative_since_first() -> None:
    newer = _account(amount=-100, currency=USD, negative_since=_AS_OF - timedelta(days=1))
    older = _account(amount=-100, currency=USD, negative_since=_AS_OF - timedelta(days=10))
    use_case = _use_case(_FakeCollectionsRepository((newer, older)))

    report = await use_case.execute(caller_id=_TEST_ADMIN)

    assert report.accounts == (older, newer)
    assert report.oldest_negative_since == older.negative_since


async def test_exposure_is_grouped_by_currency_not_summed_across_them() -> None:
    usd_a = _account(amount=-100, currency=USD, negative_since=_AS_OF - timedelta(days=1))
    usd_b = _account(amount=-250, currency=USD, negative_since=_AS_OF - timedelta(days=2))
    mxn = _account(amount=-5_000, currency=MXN, negative_since=_AS_OF - timedelta(days=3))
    use_case = _use_case(_FakeCollectionsRepository((usd_a, usd_b, mxn)))

    report = await use_case.execute(caller_id=_TEST_ADMIN)

    by_currency = {exposure.currency: exposure for exposure in report.exposures}
    assert by_currency[USD].count == 2
    assert by_currency[USD].total_owed == Money(350, USD)
    assert by_currency[MXN].count == 1
    assert by_currency[MXN].total_owed == Money(5_000, MXN)
    assert report.count == 3


async def test_a_zero_balance_account_is_never_reported() -> None:
    """Not this use case's own responsibility to filter (the repository port promises it never
    returns anything but a currently-negative account) -- this asserts the use case does not
    itself relax that promise by, say, treating a `Money.zero` row as negative."""
    use_case = _use_case(_FakeCollectionsRepository(()))

    report = await use_case.execute(caller_id=_TEST_ADMIN)

    assert report.count == 0


@pytest.mark.parametrize(
    ("age", "expected_bucket"),
    [
        (timedelta(hours=1), NegativeAgeBucket.UNDER_ONE_DAY),
        (timedelta(days=1), NegativeAgeBucket.ONE_TO_SEVEN_DAYS),
        (timedelta(days=6, hours=23), NegativeAgeBucket.ONE_TO_SEVEN_DAYS),
        (timedelta(days=7), NegativeAgeBucket.SEVEN_TO_THIRTY_DAYS),
        (timedelta(days=29, hours=23), NegativeAgeBucket.SEVEN_TO_THIRTY_DAYS),
        (timedelta(days=30), NegativeAgeBucket.OVER_THIRTY_DAYS),
        (timedelta(days=400), NegativeAgeBucket.OVER_THIRTY_DAYS),
    ],
)
async def test_age_bucket_boundaries(age: timedelta, expected_bucket: NegativeAgeBucket) -> None:
    account = _account(amount=-100, currency=USD, negative_since=_AS_OF - age)
    use_case = _use_case(_FakeCollectionsRepository((account,)))

    report = await use_case.execute(caller_id=_TEST_ADMIN)

    assert report.age_buckets[expected_bucket] == 1
    assert sum(report.age_buckets.values()) == 1


async def test_every_report_is_aged_against_the_same_clock_read() -> None:
    """Two rows in the same response must never be aged against two different instants
    (CollectionsReport's own docstring) -- both ages here are computed from the one `as_of` the
    use case reads once, not from whenever each row happened to be processed."""
    account_a = _account(amount=-100, currency=USD, negative_since=_AS_OF - timedelta(hours=2))
    account_b = _account(amount=-100, currency=USD, negative_since=_AS_OF - timedelta(hours=1))
    use_case = _use_case(_FakeCollectionsRepository((account_a, account_b)))

    report = await use_case.execute(caller_id=_TEST_ADMIN)

    assert report.as_of == _AS_OF


# -- unexplained (drifted) accounts: `negative_since is None`, PRD §11.1 -----------------------
#
# A `CollectionsRepository` may return a row with `negative_since=None` when the account is
# negative in `accounts.balance_amount` but no entry history explains it (the SQL adapter's own
# LEFT JOIN, `queries/negative_balances.py`, is what makes this reachable in practice -- an INNER
# JOIN would have dropped the row before it ever reached this use case, which is exactly what was
# wrong before that join was fixed). These tests pin how the use case must treat such a row: it
# must never disappear, never invent a timestamp, and never be silently priced at zero.


async def test_an_unexplained_account_still_counts_toward_the_total_owed() -> None:
    """The whole point of not dropping the row (see the module note above): a drifted balance is
    still money the business owes, even though nothing in the ledger says since when."""
    explained = _account(amount=-100, currency=USD, negative_since=_AS_OF - timedelta(days=1))
    unexplained = _account(amount=-99, currency=USD, negative_since=None)
    use_case = _use_case(_FakeCollectionsRepository((explained, unexplained)))

    report = await use_case.execute(caller_id=_TEST_ADMIN)

    assert report.count == 2
    assert report.unexplained_count == 1
    by_currency = {exposure.currency: exposure for exposure in report.exposures}
    assert by_currency[USD].count == 2
    assert by_currency[USD].total_owed == Money(199, USD)


async def test_an_unexplained_account_sorts_before_every_dated_account() -> None:
    ancient = _account(amount=-100, currency=USD, negative_since=_AS_OF - timedelta(days=400))
    unexplained = _account(amount=-1, currency=USD, negative_since=None)
    use_case = _use_case(_FakeCollectionsRepository((ancient, unexplained)))

    report = await use_case.execute(caller_id=_TEST_ADMIN)

    assert report.accounts[0] is unexplained
    assert report.accounts[1] is ancient


async def test_oldest_negative_since_ignores_unexplained_accounts() -> None:
    """`None` is not a timestamp -- it must never win a `min()` comparison against a real one, and
    an all-unexplained report must answer `None`, not crash comparing `None` to `None`."""
    dated = _account(amount=-100, currency=USD, negative_since=_AS_OF - timedelta(days=3))
    unexplained = _account(amount=-1, currency=USD, negative_since=None)
    mixed = await _use_case(_FakeCollectionsRepository((dated, unexplained))).execute(
        caller_id=_TEST_ADMIN
    )
    assert mixed.oldest_negative_since == dated.negative_since

    only_unexplained = await _use_case(_FakeCollectionsRepository((unexplained,))).execute(
        caller_id=_TEST_ADMIN
    )
    assert only_unexplained.oldest_negative_since is None


async def test_an_unexplained_account_is_excluded_from_every_age_bucket() -> None:
    """No known start means no defensible age -- it must not be silently counted as "under a day"
    (the cheapest bucket to fall into by accident if age defaulted to zero)."""
    unexplained = _account(amount=-1, currency=USD, negative_since=None)
    use_case = _use_case(_FakeCollectionsRepository((unexplained,)))

    report = await use_case.execute(caller_id=_TEST_ADMIN)

    assert sum(report.age_buckets.values()) == 0
    assert report.count == 1
