from urllib.parse import quote_plus

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from the environment.

    `database_url` uses SQLAlchemy's psycopg3 dialect (`postgresql+psycopg`)
    so the same driver serves both the application's async engine (AO5) and
    Alembic's async migrations — `create_async_engine` picks async usage of
    that driver up on its own, no separate "async" dialect name exists.
    The default matches `docker-compose.yml`, so `docker compose up` plus no
    `.env` file is enough for local development.

    **Why the discrete `db_*` fields exist.** In AWS the credentials come from a Secrets Manager
    secret that RDS itself generates and rotates, and its shape is fixed: a JSON document of
    `username`/`password`/`host`/`port`/`dbname`. ECS can inject one JSON key per environment
    variable, and that is all it can do — it cannot assemble those five values into a URL. So
    either the URL is duplicated into a second, hand-maintained secret that rotation would then
    silently desynchronize, or the process composes it. Composing it here is the version where
    rotation keeps working.

    An explicit `DATABASE_URL` always wins, so nothing about local development or the test suite
    changes.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/account_balance"

    db_host: str | None = None
    db_port: int = 5432
    db_user: str | None = None
    db_password: str | None = None
    db_name: str | None = None

    @model_validator(mode="after")
    def _compose_database_url_from_parts(self) -> Settings:
        """Builds `database_url` from the discrete fields, when they are the ones supplied.

        `quote_plus` on user and password is not defensive tidiness: a generated password
        containing `@`, `/` or `:` would otherwise terminate the URL's authority section early,
        and the failure surfaces as an unparseable host rather than as a credential problem.
        """
        # `model_fields_set` holds the fields the environment actually supplied, which is a
        # different question from "does this differ from the default" -- an operator who sets
        # DATABASE_URL to exactly the default value still meant to set it.
        if "database_url" in self.model_fields_set:
            return self

        # Bound to locals so the `None` checks below narrow the types -- an `assert` would too,
        # and `python -O` would strip it, turning a configuration error into a `TypeError` deep
        # inside string formatting.
        host, user, password = self.db_host, self.db_user, self.db_password

        if host is None and user is None and password is None:
            # Nothing was injected: local development, or the test suite.
            return self

        if host is None or user is None or password is None:
            # A partially injected secret is a misconfiguration, never a preference. Falling back
            # to the development default here would start the service against `localhost` with
            # `postgres`/`postgres` -- succeeding at boot, passing its own liveness probe, and
            # failing only once real money moved. Refusing to start is the cheaper failure by a
            # wide margin.
            missing = [
                name
                for name, value in (
                    ("DB_HOST", host),
                    ("DB_USER", user),
                    ("DB_PASSWORD", password),
                )
                if value is None
            ]
            raise ValueError(
                f"incomplete database configuration: {', '.join(missing)} missing while the "
                "others were supplied. Set all of DB_HOST/DB_USER/DB_PASSWORD, or set "
                "DATABASE_URL instead."
            )

        database = self.db_name or "account_balance"
        credentials = f"{quote_plus(user)}:{quote_plus(password)}"
        self.database_url = (
            f"postgresql+psycopg://{credentials}@{host}:{self.db_port}/{quote_plus(database)}"
        )
        return self
