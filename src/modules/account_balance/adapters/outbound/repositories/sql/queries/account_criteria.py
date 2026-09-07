from sqlalchemy import Select, select

from modules.account_balance.adapters.outbound.repositories.sql.dbos.models import AccountDbo
from modules.account_balance.application.gateways.models.find_accounts_criteria import (
    FindAccountByAccountId,
    FindAccountByOwnerAndPurposeAndCurrency,
    FindAccountCriteria,
)


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

    raise ValueError(f"Invalid criteria: {criteria}")
