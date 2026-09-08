import asyncio
import os
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

# `alembic.ini`'s `sqlalchemy.url` is the docker-compose default. A
# programmatic caller (integration tests, pointing migrations at their own
# testcontainers-provisioned PostgreSQL) wins by passing the url through
# `config.attributes` -- checked first, so it is never shadowed by whatever
# `DATABASE_URL` happens to be set in the developer's own shell. Only when
# no attribute override is given does an explicit `DATABASE_URL` env var
# (the same variable `Settings` reads) override the ini default, for normal
# non-test invocations of `alembic upgrade`.
if "sqlalchemy.url" in config.attributes:
    config.set_main_option("sqlalchemy.url", config.attributes["sqlalchemy.url"])
elif os.environ.get("DATABASE_URL"):
    config.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])


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


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
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
