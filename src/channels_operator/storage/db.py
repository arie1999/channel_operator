"""SQLAlchemy engine creation and table init.

The engine URL comes from `settings.database_url`. SaaS-readiness: swap
SQLite for Postgres by changing only DATABASE_URL — schema is portable.
"""

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from channels_operator.settings import settings
from channels_operator.storage.models import Base

engine = create_engine(settings.database_url, echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def init_db() -> None:
    """Ensure tables exist. Idempotent. SQLite needs the parent
    directory of the db file to exist; this handles that case."""
    if settings.database_url.startswith("sqlite:///"):
        path_part = settings.database_url[len("sqlite:///"):]
        if path_part:
            Path(path_part).parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)
