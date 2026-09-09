"""`Settings` composing `database_url` from the discrete `db_*` fields.

That branch only ever executes in AWS, where ECS injects one environment variable per key of the
RDS-generated secret (`infra/lib/service-stack.ts`). Nothing in local development or the
integration suite exercises it, so without these tests the first time it runs would be in
production, against real money.
"""

import pytest
from pydantic import ValidationError

from modules.shared.adapters.config.settings import Settings

_DEFAULT = "postgresql+psycopg://postgres:postgres@localhost:5432/account_balance"

_ENVIRONMENT_KEYS = ("DATABASE_URL", "DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME")


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """`Settings` reads the process environment by design, so a developer who happens to export
    `DATABASE_URL` would otherwise fail these tests -- and the failure would look like a bug in
    the composition logic rather than in the environment they are running from."""
    for key in _ENVIRONMENT_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_the_default_url_is_used_when_no_parts_are_supplied() -> None:
    """Local development and the test suite: nothing set, `docker-compose.yml`'s database."""
    assert Settings().database_url == _DEFAULT


def test_the_url_is_composed_from_the_injected_secret_keys() -> None:
    settings = Settings(
        db_host="ledger.abc123.us-east-1.rds.amazonaws.com",
        db_port=5432,
        db_user="account_balance_app",
        db_password="s3cret",
        db_name="account_balance",
    )

    assert settings.database_url == (
        "postgresql+psycopg://account_balance_app:s3cret"
        "@ledger.abc123.us-east-1.rds.amazonaws.com:5432/account_balance"
    )


def test_a_password_with_url_significant_characters_is_percent_encoded() -> None:
    """The failure this prevents is not cosmetic. An unescaped `@` ends the URL's authority
    section early, so the driver reports an unresolvable host -- a DNS-shaped error for what is
    actually a credential, sending whoever is on call to the wrong system entirely."""
    settings = Settings(
        db_host="ledger.rds.amazonaws.com",
        db_user="app@platform",
        db_password="p@ss:w/rd",
    )

    assert (
        settings.database_url
        == "postgresql+psycopg://app%40platform:p%40ss%3Aw%2Frd@ledger.rds.amazonaws.com:5432/account_balance"
    )


def test_an_explicit_url_wins_over_the_parts() -> None:
    """An operator who sets `DATABASE_URL` means it -- pointing a task at a replica or a restored
    snapshot has to be possible without unsetting five other variables first."""
    settings = Settings(
        database_url="postgresql+psycopg://someone:else@replica.internal:6432/other",
        db_host="ledger.rds.amazonaws.com",
        db_user="account_balance_app",
        db_password="s3cret",
    )

    assert settings.database_url == "postgresql+psycopg://someone:else@replica.internal:6432/other"


@pytest.mark.parametrize(
    "missing",
    ["db_host", "db_user", "db_password"],
    ids=["without host", "without user", "without password"],
)
def test_a_partial_secret_refuses_to_start(missing: str) -> None:
    """The failure this prevents is the quietest one available.

    Falling back to the development default here would start the service against `localhost` with
    `postgres`/`postgres` -- it would boot, pass its own liveness probe, register healthy with the
    load balancer, and only reveal the problem once someone moved money. A misconfigured secret is
    not a preference for the default; refusing to start is by far the cheaper failure.
    """
    parts = {
        "db_host": "ledger.rds.amazonaws.com",
        "db_user": "account_balance_app",
        "db_password": "s3cret",
    }
    del parts[missing]

    with pytest.raises(ValidationError, match="incomplete database configuration"):
        Settings(**parts)  # type: ignore[arg-type]


def test_a_database_name_needing_escaping_is_percent_encoded() -> None:
    """Same reasoning as the credentials, applied to the last free-text field in the URL."""
    settings = Settings(
        db_host="ledger.rds.amazonaws.com",
        db_user="app",
        db_password="pw",
        db_name="ledger db",
    )

    assert settings.database_url.endswith("/ledger+db")


def test_a_non_numeric_port_is_rejected_rather_than_coerced() -> None:
    """`DB_PORT` arrives as a string from Secrets Manager, so the conversion is real work, and a
    malformed one should fail at startup rather than at the first connection attempt."""
    with pytest.raises(ValidationError):
        Settings(db_port="not-a-port")
