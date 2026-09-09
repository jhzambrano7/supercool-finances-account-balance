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

    # --- Connection pool -----------------------------------------------------------------
    #
    # Sized explicitly rather than left to SQLAlchemy's defaults (`pool_size` 5, `max_overflow`
    # 10). Those defaults are a reasonable general-purpose web-app choice from the synchronous-ORM
    # era; they are not a decision about *this* workload, and leaving them implicit made the
    # deployment's `maxCapacity` depend on a library default nobody in this package had ever
    # declared (`infra/stacks/service_stack.py`).

    #: Connections held open per process. One engine per process (AO5), so this is the whole
    #: footprint of one ECS task. The deployed value comes from the infrastructure, which derives
    #: the task ceiling from the same number; this default is for local development.
    db_pool_size: int = 10

    #: **Zero on purpose, and this is the load-bearing choice.**
    #:
    #: Overflow exists to absorb a burst shorter than the time capacity takes to arrive. It does
    #: not help here, for reasons that compound:
    #:
    #: - The bottleneck is not the connection, it is the row lock. A second transfer against the
    #:   same account blocks on `SELECT ... FOR UPDATE` whether or not it holds a connection.
    #:   Handing it one only moves the wait *inside* PostgreSQL, where it occupies a backend
    #:   process, a snapshot and a lock-manager slot. Waiting in the pool queue occupies nothing.
    #: - PostgreSQL throughput is not monotonic in connection count. Past a small multiple of the
    #:   cores (two, on `db.t4g.medium`), more concurrent backends *reduce* total throughput
    #:   through context switching, lock-manager contention and visibility checks. Overflow
    #:   connections therefore degrade the requests that already had one.
    #: - They are created on demand — TCP, TLS, and a forked backend process — so the cost lands
    #:   as added latency during the burst, which is when latency matters most.
    #: - They make capacity undecidable: the fleet's connection count has a steady answer and a
    #:   peak answer, so every safety margin must use the peak while only the steady state is
    #:   enjoyed. Overflow only saves money if the database is sized for the steady state, which
    #:   for a ledger is exactly what must not be done.
    #:
    #: With overflow at zero and a short `db_pool_timeout_seconds`, a saturated task fails fast
    #: with a local, observable signal and the caller retries safely (§6's idempotency makes that
    #: safe by construction). The alternative failure is connection refusal at the database, which
    #: hits every task including the healthy ones and locks operators out mid-incident.
    db_max_overflow: int = 0

    #: How long a request waits for a pooled connection before giving up. Far below SQLAlchemy's
    #: 30-second default: thirty seconds is a request whose caller has already retried twice.
    #: Because every mutating request carries an idempotency key, failing fast returns control to
    #: a client that can safely retry — which beats holding a request open on the hope that a slot
    #: frees up.
    db_pool_timeout_seconds: float = 5.0

    #: Verify a pooled connection before handing it out. The cost is one round trip per checkout;
    #: what it buys is the RDS Multi-AZ failover we deliberately chose. A failover severs every
    #: established connection, and without this the first request to pick up each stale one fails
    #: with a driver error — a 500 on a money movement, at the moment of least tolerance.
    db_pool_pre_ping: bool = True

    #: Recycle connections older than this. Guards against the silent half-open connections that
    #: NAT gateways and load balancers leave behind on idle TCP sessions.
    db_pool_recycle_seconds: int = 1800

    @property
    def max_database_connections_per_process(self) -> int:
        """The whole connection footprint of one process — what the infrastructure's task ceiling
        is divided out of. A property rather than a comment somewhere else, so the two cannot
        drift."""
        return self.db_pool_size + self.db_max_overflow

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
