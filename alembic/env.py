import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Imported for their side effect: importing a module registers its DBO class
# on `Base.metadata` (SQLAlchemy declarative registration happens at
# class-definition time). Every DBO module must be listed here -- one per
# DBO, so a new one is a new line -- or autogenerate silently won't see its
# table.
import modules.account_balance.adapters.outbound.repositories.sql.dbos.account_dbo
import modules.account_balance.adapters.outbound.repositories.sql.dbos.entry_dbo
import modules.account_balance.adapters.outbound.repositories.sql.dbos.idempotency_record_dbo
import modules.account_balance.adapters.outbound.repositories.sql.dbos.transfer_dbo  # noqa: F401
from modules.shared.adapters.config.settings import Settings
from modules.shared.adapters.outbound.repositories.sql.base import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# `Base` is shared across every module (modules.shared.adapters.outbound.
# repositories.sql.base) so autogenerate sees the whole project's schema,
# not just one module's.
target_metadata = Base.metadata

# Where migrations connect, in one place.
#
# A programmatic caller (integration tests, pointing migrations at their own
# testcontainers-provisioned PostgreSQL) wins by passing the url through
# `config.attributes` -- checked first, so it is never shadowed by whatever
# happens to be set in the developer's own shell.
#
# Everything else defers to `Settings`, which is the same object the running
# service uses. This is deliberate and it was a real bug before: reading
# `DATABASE_URL` here directly meant AWS never matched, because ECS injects the
# RDS secret as five discrete `DB_*` variables and cannot assemble a URL from
# them (infra/stacks/service_stack.py). Alembic then fell through to
# `alembic.ini`'s value and tried to migrate `localhost`. One source of truth
# removes the possibility of the migration and the service disagreeing about
# which database they are talking to.
if "sqlalchemy.url" in config.attributes:
    config.set_main_option("sqlalchemy.url", config.attributes["sqlalchemy.url"])
else:
    config.set_main_option("sqlalchemy.url", Settings().database_url)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


# An arbitrary but fixed key: any two processes using this same number serialize
# against each other, and nothing else in the database uses it.
_MIGRATION_LOCK_KEY = 0x4C45444745524D47  # "LEDGERMG"


def do_run_migrations(connection: Connection) -> None:
    """Runs the pending migrations, holding an advisory lock for the duration.

    Alembic itself takes no lock. Two concurrent `alembic upgrade head` runs -- two deploys, a
    human and CI, or one deploy retried while the first is still going -- both read
    `alembic_version`, both decide the same revision is pending, and the loser fails partway
    through against a schema the winner is still rewriting. For a ledger, a half-applied migration
    is the worst possible state to be left in.

    `pg_advisory_xact_lock` blocks rather than failing, and is released when the transaction ends
    however it ends -- including a crash, which a lock table of our own would not survive.
    """
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        # Inside the transaction, before any migration runs: `pg_advisory_xact_lock` is scoped to
        # the transaction, so taking it earlier would release it immediately.
        connection.exec_driver_sql(f"SELECT pg_advisory_xact_lock({_MIGRATION_LOCK_KEY})")
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
