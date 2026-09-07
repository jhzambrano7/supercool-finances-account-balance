from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base shared by every module's DBOs — Alembic's
    `target_metadata` walks this one base's registry, so every module's
    tables autogenerate together instead of each module fragmenting its own
    metadata.
    """
