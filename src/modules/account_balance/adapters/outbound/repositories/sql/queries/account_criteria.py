from sqlalchemy import Select, select

from modules.account_balance.adapters.outbound.repositories.sql.dbos.account_dbo import AccountDbo
from modules.account_balance.application.gateways.models.find_accounts_criteria import (
    FindAccountByAccountId,
    FindAccountByOwnerAndPurposeAndCurrency,
    FindAccountCriteria,
    FindSystemAccountByPurposeAndCurrency,
)
from modules.account_balance.domain.account import AccountType


def find_account_criteria_to_sql_query(criteria: FindAccountCriteria) -> Select[tuple[AccountDbo]]:
    """Translates a `FindAccountCriteria` into the query that answers it --
    the one place this module's criteria vocabulary meets SQLAlchemy.
    """
    match criteria:
        case FindAccountByOwnerAndPurposeAndCurrency(owner_id, purpose, currency):
            return select(AccountDbo).where(
                AccountDbo.owner_id == owner_id.value,
                AccountDbo.purpose == purpose.value,
                AccountDbo.currency == str(currency),
            )
        case FindAccountByAccountId(account_id):
            return select(AccountDbo).where(AccountDbo.account_id == account_id.value)
        case FindSystemAccountByPurposeAndCurrency(purpose, currency):
            # account_type == SYSTEM is redundant with the criteria's own
            # construction-time validation (purpose already implies SYSTEM),
            # but stated here too: this WHERE clause is what actually keeps
            # a USER row unreachable through this query, not just the type
            # that built it.
            return select(AccountDbo).where(
                AccountDbo.account_type == AccountType.SYSTEM.value,
                AccountDbo.purpose == purpose.value,
                AccountDbo.currency == str(currency),
            )

    raise ValueError(f"Invalid criteria: {criteria}")
