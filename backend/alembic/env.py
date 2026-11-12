"""Alembic migration environment.

The database URL is read from the process environment / ``backend/.env`` via
:func:`app.config.load_env` and the ``Settings`` snapshot; it is injected into
Alembic at runtime so it is never stored in ``alembic.ini``.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make the backend package importable when running alembic from backend/.
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import load_env, get_settings  # noqa: E402
from app.database.models.base import Base  # noqa: E402
from app.database.models import *  # noqa: E402,F401,F403  (register all tables)

load_env()
settings = get_settings()

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    url = config.get_main_option("sqlalchemy.url", "")
    if not url:
        url = settings.database_url
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not configured. Set it in the environment or "
            "backend/.env before running Alembic."
        )
    # Never echo the URL (it contains credentials).
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Inject the runtime URL without writing it to alembic.ini.
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
