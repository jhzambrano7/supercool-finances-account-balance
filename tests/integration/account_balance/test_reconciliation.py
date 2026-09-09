"""Reconciliation: `accounts.balance_amount == SUM(entries)` for every `USER` account, after
every money-movement path -- PRD §10, success criterion 2.

This is *not* a test of double-entry integrity. I1 (per transfer, debits equal credits) is enforced
in the domain and cannot be violated by construction; it is asserted here only as the cheap
companion check.

What this file exists for is the cost PRD §5.1 accepted explicitly. The balance is a **materialized
projection**: entries are the source of truth, and `accounts.balance_amount` is a denormalized copy
written in the same transaction. Two storage locations, and only a write path keeps them equal. A
ledger can balance perfectly while that column lies -- and no domain invariant can see it, because
the domain never reads the column back.

That is not hypothetical here. `SqlAccountRepository.update()` shipped with `status` silently
missing from its `.values(...)`: it wrote `balance_amount` and `version` and dropped the third
column on the floor. One column over, and this is the only check that would have caught it.

`SYSTEM` accounts are deliberately excluded from the drift check and asserted separately: per §5.3
they never materialize a balance at all, so for them `SUM(entries)` *is* the balance and there is
nothing to reconcile. That difference is the point -- it is what materializing buys and costs.
"""

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from modules.account_balance.adapters.config.admin_principal import ADMIN_PRINCIPAL_ID

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

_ADMIN_ID = str(ADMIN_PRINCIPAL_ID)

# One account holds exactly one currency (AO4's natural key), and one transfer moves exactly one
# (T9), so summing bare entry amounts per account or per transfer never mixes currencies -- which
# is why `entries` can get away with carrying no currency column of its own.
_LEDGER_POSITION = text(
    """
    SELECT a.account_id,
           a.balance_amount,
           -- `::bigint`: SUM over a bigint yields numeric in PostgreSQL, and a `Decimal` in the
           -- failure message reads like the balance is a different kind of number than it is.
           COALESCE(
               SUM(CASE WHEN e.direction = 'CREDIT' THEN e.amount ELSE -e.amount END), 0
           )::bigint AS ledger_position
    FROM accounts a
    LEFT JOIN entries e ON e.account_id = a.account_id
    WHERE a.account_type = 'USER'
    GROUP BY a.account_id, a.balance_amount
    """
)

_TRANSFER_NET = text(
    """
    SELECT transfer_id,
           SUM(CASE WHEN direction = 'CREDIT' THEN amount ELSE -amount END)::bigint AS net
    FROM entries
    GROUP BY transfer_id
    """
)

_SYSTEM_BALANCES = text(
    "SELECT account_id, balance_amount FROM accounts WHERE account_type = 'SYSTEM'"
)


async def _assert_reconciled(engine: AsyncEngine) -> None:
    """The whole invariant, over whatever the database currently holds.

    Deliberately global rather than scoped to one account id: a write path that credits the right
    account and corrupts a different one is exactly the failure a per-account assertion would wave
    through. `_clean_tables` gives each test an empty slate, so "whatever the database holds" is
    always precisely what the test under way just wrote.
    """
    async with engine.connect() as connection:
        drifted = [
            (str(row.account_id), row.balance_amount, row.ledger_position)
            for row in (await connection.execute(_LEDGER_POSITION)).all()
            if row.balance_amount != row.ledger_position
        ]
        assert not drifted, (
            "USER accounts whose materialized balance drifted from the ledger "
            f"(account_id, balance_amount, SUM(entries)): {drifted}"
        )

        unbalanced = [
            (str(row.transfer_id), row.net)
            for row in (await connection.execute(_TRANSFER_NET)).all()
            if row.net != 0
        ]
        assert not unbalanced, f"transfers whose entries do not net to zero (I1): {unbalanced}"

        # §5.3: a SYSTEM account's column is seeded at zero and never written. A non-zero one means
        # some path started maintaining it -- reintroducing the global contention §5.3 removed.
        non_zero_system = [
            (str(row.account_id), row.balance_amount)
            for row in (await connection.execute(_SYSTEM_BALANCES)).all()
            if row.balance_amount != 0
        ]
        assert not non_zero_system, (
            "SYSTEM accounts must never materialize a balance (PRD §5.3); "
            f"these did: {non_zero_system}"
        )


def _headers(*, caller_id: str, idempotency_key: str) -> dict[str, str]:
    return {"X-Caller-Id": caller_id, "Idempotency-Key": idempotency_key}


async def _open_user_account(client: AsyncClient, *, owner_id: str) -> str:
    response = await client.post(
        "/accounts", json={"owner_id": owner_id, "purpose": "CHECKING", "currency": "USD"}
    )
    assert response.status_code == 201, response.text
    account_id: str = response.json()["account_id"]
    return account_id


async def _deposit(client: AsyncClient, *, account_id: str, owner_id: str, amount: int) -> str:
    response = await client.post(
        "/deposits",
        json={"destination_account_id": account_id, "amount": amount, "currency": "USD"},
        headers=_headers(caller_id=owner_id, idempotency_key=str(uuid4())),
    )
    assert response.status_code == 201, response.text
    transfer_id: str = response.json()["transfer_id"]
    return transfer_id


async def _withdraw(client: AsyncClient, *, account_id: str, owner_id: str, amount: int) -> str:
    response = await client.post(
        "/withdrawals",
        json={"source_account_id": account_id, "amount": amount, "currency": "USD"},
        headers=_headers(caller_id=owner_id, idempotency_key=str(uuid4())),
    )
    assert response.status_code == 201, response.text
    transfer_id: str = response.json()["transfer_id"]
    return transfer_id


async def test_a_deposit_leaves_the_balance_reconciled(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    """A deposit credits a `USER` account and debits `FUNDING`. The `USER` side materializes,
    the `SYSTEM` side does not -- both must hold."""
    owner_id = str(uuid4())
    account_id = await _open_user_account(client, owner_id=owner_id)

    await _deposit(client, account_id=account_id, owner_id=owner_id, amount=5_000)

    await _assert_reconciled(engine)


async def test_a_withdrawal_leaves_the_balance_reconciled(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    """The debit path -- the one where a drifting column would let a customer overdraw, because
    I2 is checked against the materialized balance, not against `SUM(entries)`."""
    owner_id = str(uuid4())
    account_id = await _open_user_account(client, owner_id=owner_id)
    await _deposit(client, account_id=account_id, owner_id=owner_id, amount=10_000)

    await _withdraw(client, account_id=account_id, owner_id=owner_id, amount=2_500)

    await _assert_reconciled(engine)


async def test_a_customer_to_customer_transfer_leaves_both_balances_reconciled(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    """Two materialized balances written in one transaction -- the case where a partial write
    would leave the ledger balanced and exactly one column wrong."""
    owner_a, owner_b = str(uuid4()), str(uuid4())
    account_a = await _open_user_account(client, owner_id=owner_a)
    account_b = await _open_user_account(client, owner_id=owner_b)
    await _deposit(client, account_id=account_a, owner_id=owner_a, amount=8_000)

    response = await client.post(
        "/transfers",
        json={
            "source_account_id": account_a,
            "destination_account_id": account_b,
            "amount": 3_000,
            "currency": "USD",
        },
        headers=_headers(caller_id=owner_a, idempotency_key=str(uuid4())),
    )
    assert response.status_code == 201, response.text

    await _assert_reconciled(engine)


async def test_a_reversal_leaves_the_balance_reconciled(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    """Reversal is the only path allowed to drive a `USER` balance negative (§7.3), so it is the
    one path where "the balance looks wrong" is not by itself evidence of a bug -- which is
    exactly why it needs the ledger to confirm the number is the right kind of wrong."""
    owner_a, owner_b = str(uuid4()), str(uuid4())
    account_a = await _open_user_account(client, owner_id=owner_a)
    account_b = await _open_user_account(client, owner_id=owner_b)
    await _deposit(client, account_id=account_a, owner_id=owner_a, amount=6_000)

    transfer = await client.post(
        "/transfers",
        json={
            "source_account_id": account_a,
            "destination_account_id": account_b,
            "amount": 4_000,
            "currency": "USD",
        },
        headers=_headers(caller_id=owner_a, idempotency_key=str(uuid4())),
    )
    assert transfer.status_code == 201, transfer.text
    transfer_id = transfer.json()["transfer_id"]
    # Spend what was received, so the reversal has to take account_b below zero.
    await _withdraw(client, account_id=account_b, owner_id=owner_b, amount=4_000)

    reversal = await client.post(
        f"/transfers/{transfer_id}/reversals",
        headers=_headers(caller_id=_ADMIN_ID, idempotency_key=str(uuid4())),
    )
    assert reversal.status_code == 201, reversal.text

    await _assert_reconciled(engine)


async def test_the_balance_reconciles_after_an_arbitrary_sequence_of_operations(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    """PRD §10 criterion 2 says "after any sequence of operations", not "after one". Interleaving
    the paths is what catches a drift that only appears when one write path follows another --
    a stale in-memory `version`, or a second update overwriting the first's column."""
    owner_a, owner_b = str(uuid4()), str(uuid4())
    account_a = await _open_user_account(client, owner_id=owner_a)
    account_b = await _open_user_account(client, owner_id=owner_b)

    await _deposit(client, account_id=account_a, owner_id=owner_a, amount=10_000)
    await _deposit(client, account_id=account_b, owner_id=owner_b, amount=1_000)
    await _withdraw(client, account_id=account_a, owner_id=owner_a, amount=2_000)

    for amount in (500, 1_500, 250):
        response = await client.post(
            "/transfers",
            json={
                "source_account_id": account_a,
                "destination_account_id": account_b,
                "amount": amount,
                "currency": "USD",
            },
            headers=_headers(caller_id=owner_a, idempotency_key=str(uuid4())),
        )
        assert response.status_code == 201, response.text

    await _withdraw(client, account_id=account_b, owner_id=owner_b, amount=3_000)
    await _deposit(client, account_id=account_a, owner_id=owner_a, amount=750)

    await _assert_reconciled(engine)


async def test_a_replayed_deposit_does_not_double_count_the_balance(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    """Idempotency and reconciliation meet here: a replay that re-applied the balance update while
    reusing the original entries would leave the column ahead of the ledger by exactly one
    deposit -- a drift no idempotency assertion on the *response* can see."""
    owner_id = str(uuid4())
    account_id = await _open_user_account(client, owner_id=owner_id)
    key = str(uuid4())
    payload = {"destination_account_id": account_id, "amount": 5_000, "currency": "USD"}
    headers = _headers(caller_id=owner_id, idempotency_key=key)

    first = await client.post("/deposits", json=payload, headers=headers)
    assert first.status_code == 201, first.text
    replay = await client.post("/deposits", json=payload, headers=headers)
    assert replay.status_code in (200, 201), replay.text
    assert replay.json()["transfer_id"] == first.json()["transfer_id"]

    await _assert_reconciled(engine)
