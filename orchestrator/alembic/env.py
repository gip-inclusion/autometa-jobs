"""Alembic environment for autometa-jobs.

Reads the database URL from $PIPOMETA_DATABASE_URL (the same env var the
orchestrator uses), normalising the asyncpg driver to psycopg sync since
Alembic runs sync DDL.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make the orchestrator package importable so we can use Base.metadata.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from orchestrator.models import Base  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolve_db_url() -> str:
    url = os.environ.get("PIPOMETA_DATABASE_URL", "")
    if not url:
        raise RuntimeError("PIPOMETA_DATABASE_URL must be set for alembic")
    # The app uses asyncpg; alembic needs a sync driver. Switch to psycopg v3
    # and translate the asyncpg-style `ssl=...` query param to psycopg's `sslmode=...`.
    url = url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    url = url.replace("?ssl=require", "?sslmode=require").replace("&ssl=require", "&sslmode=require")
    return url


def run_migrations_offline() -> None:
    context.configure(url=_resolve_db_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    cfg = config.get_section(config.config_ini_section) or {}
    cfg["sqlalchemy.url"] = _resolve_db_url()
    connectable = engine_from_config(cfg, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
