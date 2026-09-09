from sqlalchemy import CTE, Select, case, func, select

from modules.account_balance.adapters.outbound.repositories.sql.dbos.account_dbo import AccountDbo
from modules.account_balance.adapters.outbound.repositories.sql.dbos.entry_dbo import EntryDbo
from modules.account_balance.domain.account import AccountType
from modules.account_balance.domain.entry import EntryDirection

# --------------------------------------------------------------------------------------------
# How "negative since" is computed, and why it lives here rather than in Python.
#
# The naive answers are both wrong: "the timestamp of the oldest debit" ignores that the account
# may have been topped back up to >= 0 since; "the entry that first made it negative" has the same
# problem the moment there is a *second* negative episode -- it reports the first one forever.
# What PRD §11.3 actually wants is the start of the *current, still-open* negative episode.
#
# That is a running-balance question: walk an account's entries in order, and find the last point
# where the running balance crossed from >= 0 to < 0 and has stayed negative since (which it must
# have, given the account is negative right now -- entries are the only thing that can move a
# balance, PRD §5.1). A window function expresses this directly and once, in the one engine that
# already holds the whole ledger; recomputing the same running sum in Python would mean pulling
# every entry for every negative account across the network first, for a computation SQL is
# already built to do in one round trip. Three CTEs, each answering one question:
#
#   1. negative_accounts  -- which USER accounts are negative right now (cheap: an indexed-ready
#      column comparison on the materialized balance, PRD §5.1 -- no ledger scan needed for this
#      part).
#   2. running_balance    -- for each entry of those accounts only (not the whole ledger), the
#      running balance up to and including that entry, oldest first.
#   3. episode_starts      -- pairs each row with the *previous* row's running balance (`LAG`,
#      defaulting to 0 for an account's very first entry -- every account is born at zero, PRD
#      §4.1/`UserAccount.open`), so "did this entry cross zero" is a single row-level comparison.
#
# The final SELECT takes MAX(occurred_at) among "episode start" rows per account -- the *last*
# crossing, which is what makes a recovered-then-negative-again account report its second episode,
# not its first (there is more than one qualifying row for such an account; MAX picks the most
# recent).
# --------------------------------------------------------------------------------------------


def _negative_user_accounts_cte() -> CTE:
    return (
        select(
            AccountDbo.account_id,
            AccountDbo.owner_id,
            AccountDbo.currency,
            AccountDbo.balance_amount,
        )
        .where(
            AccountDbo.account_type == AccountType.USER.value,
            AccountDbo.balance_amount < 0,
        )
        .cte("negative_accounts")
    )


def _running_balance_cte(negative_accounts: CTE) -> CTE:
    # The sign is derived exactly once (D2, `Entry.signed_amount`'s own reasoning) -- this is the
    # SQL-side mirror of that same rule, not a second place that decides it.
    signed_amount = case(
        (EntryDbo.direction == EntryDirection.CREDIT.value, EntryDbo.amount),
        else_=-EntryDbo.amount,
    )
    return (
        select(
            EntryDbo.account_id.label("account_id"),
            EntryDbo.occurred_at.label("occurred_at"),
            EntryDbo.entry_id.label("entry_id"),
            func.sum(signed_amount)
            .over(
                partition_by=EntryDbo.account_id,
                order_by=(EntryDbo.occurred_at, EntryDbo.entry_id),
                # Explicit, not the default RANGE frame: ROWS BETWEEN UNBOUNDED PRECEDING AND
                # CURRENT ROW is the only frame that means "every entry up to and including this
                # one", regardless of how the default frame would treat ties on `occurred_at`.
                rows=(None, 0),
            )
            .label("running_balance"),
        )
        .select_from(EntryDbo)
        .join(negative_accounts, negative_accounts.c.account_id == EntryDbo.account_id)
        .cte("running_balance")
    )


def _episode_starts_cte(running_balance: CTE) -> CTE:
    previous_balance = func.lag(running_balance.c.running_balance, 1, 0).over(
        partition_by=running_balance.c.account_id,
        order_by=(running_balance.c.occurred_at, running_balance.c.entry_id),
    )
    return select(
        running_balance.c.account_id,
        running_balance.c.occurred_at,
        running_balance.c.running_balance,
        previous_balance.label("previous_balance"),
    ).cte("episode_starts")


def negative_balances_query() -> Select[tuple[object, object, str, int, object]]:
    """One statement, three CTEs (see the module docstring above): every currently-negative
    `USER` account, joined with the start of its *current* negative episode.
    """
    negative_accounts = _negative_user_accounts_cte()
    running_balance = _running_balance_cte(negative_accounts)
    episode_starts = _episode_starts_cte(running_balance)

    negative_since = (
        select(
            episode_starts.c.account_id,
            func.max(episode_starts.c.occurred_at).label("negative_since"),
        )
        .where(
            episode_starts.c.running_balance < 0,
            episode_starts.c.previous_balance >= 0,
        )
        .group_by(episode_starts.c.account_id)
        .cte("negative_since")
    )

    return select(
        negative_accounts.c.account_id,
        negative_accounts.c.owner_id,
        negative_accounts.c.currency,
        negative_accounts.c.balance_amount,
        negative_since.c.negative_since,
    ).join(negative_since, negative_since.c.account_id == negative_accounts.c.account_id)
