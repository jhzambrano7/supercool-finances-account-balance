from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from the environment.

    `database_url` uses SQLAlchemy's psycopg3 dialect (`postgresql+psycopg`)
    so the same driver serves both the application's async engine (AO5) and
    Alembic's async migrations — `create_async_engine` picks async usage of
    that driver up on its own, no separate "async" dialect name exists.
    The default matches `docker-compose.yml`, so `docker compose up` plus no
    `.env` file is enough for local development.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/account_balance"
