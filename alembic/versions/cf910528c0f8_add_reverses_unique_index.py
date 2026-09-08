"""add partial unique index on transfers.reverses

Revision ID: cf910528c0f8
Revises: 5bf582a92358
Create Date: 2026-09-07 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "cf910528c0f8"
down_revision: str | Sequence[str] | None = "5bf582a92358"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # R4, design.md §8: "at most one reversal per transfer" is a fact about
    # *other* rows -- exactly the class of constraint the account-balance
    # domain spec says no single aggregate can enforce (mirrors AO4's natural
    # key and T5's idempotency key). A partial index, not a plain UNIQUE
    # constraint: Postgres does not support a WHERE clause on a table
    # constraint, only on an index, and scoping to `reverses IS NOT NULL`
    # keeps ordinary transfers (whose `reverses` is always NULL) out of the
    # uniqueness entirely -- otherwise every non-reversal transfer would
    # collide with every other on the shared NULL value. Reversing a
    # reversal is still allowed: the constraint is on `reverses`' *value*,
    # not on whether a transfer *is itself* a reversal.
    op.create_index(
        "uq_transfers_reverses",
        "transfers",
        ["reverses"],
        unique=True,
        postgresql_where=sa.text("reverses IS NOT NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_transfers_reverses", table_name="transfers")
