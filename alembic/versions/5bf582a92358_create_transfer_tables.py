"""create transfer tables

Revision ID: 5bf582a92358
Revises: 38ec8c622b3f
Create Date: 2026-09-07 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from modules.account_balance.adapters.config.seeded_accounts import (
    FUNDING_ACCOUNT_ID,
    SETTLEMENT_ACCOUNT_ID,
)
from modules.account_balance.domain.identifiers import PLATFORM_OWNER_ID

# revision identifiers, used by Alembic.
revision: str = "5bf582a92358"
down_revision: str | Sequence[str] | None = "38ec8c622b3f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# T8: PLATFORM_OWNER_ID is the nil UUID (domain/identifiers.py) -- these are
# the two SYSTEM accounts it owns, seeded here since nothing else in this
# codebase can create a SYSTEM account (account-opening only opens USER
# accounts, AO1).
_PLATFORM_OWNER_ID = str(PLATFORM_OWNER_ID.value)
_FUNDING_ACCOUNT_ID = str(FUNDING_ACCOUNT_ID)
_SETTLEMENT_ACCOUNT_ID = str(SETTLEMENT_ACCOUNT_ID)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "transfers",
        sa.Column("transfer_id", sa.UUID(), nullable=False),
        sa.Column("source_account_id", sa.UUID(), nullable=False),
        sa.Column("destination_account_id", sa.UUID(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("requested_by", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reverses", sa.UUID(), nullable=True),
        sa.PrimaryKeyConstraint("transfer_id"),
        sa.ForeignKeyConstraint(["source_account_id"], ["accounts.account_id"]),
        sa.ForeignKeyConstraint(["destination_account_id"], ["accounts.account_id"]),
        sa.ForeignKeyConstraint(["reverses"], ["transfers.transfer_id"]),
    )
    op.create_table(
        "entries",
        sa.Column("entry_id", sa.UUID(), nullable=False),
        sa.Column("transfer_id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("entry_id"),
        sa.ForeignKeyConstraint(["transfer_id"], ["transfers.transfer_id"]),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.account_id"]),
    )
    op.create_index("ix_entries_account_id", "entries", ["account_id"])
    op.create_index("ix_entries_transfer_id", "entries", ["transfer_id"])
    op.create_table(
        "idempotency_records",
        sa.Column("caller_id", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("transfer_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("caller_id", "idempotency_key"),
        # Deferred to COMMIT, not checked per-statement: the use case reserves
        # this row *before* the transfer row exists (T5 -- the reservation is
        # what a losing concurrent request must collide with, before it ever
        # touches an account), and only inserts the transfer afterward, both
        # within the same transaction. An immediate FK would reject the
        # reservation insert outright.
        sa.ForeignKeyConstraint(
            ["transfer_id"], ["transfers.transfer_id"], deferrable=True, initially="DEFERRED"
        ),
    )

    # T8: seed the one FUNDING and one SETTLEMENT SYSTEM account. Both start
    # at balance_amount=0 -- correct at seed time, and irrelevant afterwards
    # since a SYSTEM account's balance is never read from or written to this
    # column from here on (T7); it stays inert rather than kept accurate.
    accounts = sa.table(
        "accounts",
        sa.column("account_id", sa.UUID()),
        sa.column("owner_id", sa.UUID()),
        sa.column("account_type", sa.String()),
        sa.column("purpose", sa.String()),
        sa.column("currency", sa.String()),
        sa.column("balance_amount", sa.BigInteger()),
        sa.column("status", sa.String()),
        sa.column("version", sa.Integer()),
    )
    op.bulk_insert(
        accounts,
        [
            {
                "account_id": _FUNDING_ACCOUNT_ID,
                "owner_id": _PLATFORM_OWNER_ID,
                "account_type": "SYSTEM",
                "purpose": "FUNDING",
                "currency": "USD",
                "balance_amount": 0,
                "status": "ACTIVE",
                "version": 0,
            },
            {
                "account_id": _SETTLEMENT_ACCOUNT_ID,
                "owner_id": _PLATFORM_OWNER_ID,
                "account_type": "SYSTEM",
                "purpose": "SETTLEMENT",
                "currency": "USD",
                "balance_amount": 0,
                "status": "ACTIVE",
                "version": 0,
            },
        ],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        sa.text("DELETE FROM accounts WHERE account_id IN (:funding, :settlement)").bindparams(
            funding=_FUNDING_ACCOUNT_ID, settlement=_SETTLEMENT_ACCOUNT_ID
        )
    )
    op.drop_table("idempotency_records")
    op.drop_index("ix_entries_transfer_id", table_name="entries")
    op.drop_index("ix_entries_account_id", table_name="entries")
    op.drop_table("entries")
    op.drop_table("transfers")
