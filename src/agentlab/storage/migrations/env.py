"""Alembic environment for AgentLab."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

from agentlab.storage.orm import Base

config = context.config
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"}, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = config.get_main_option("sqlalchemy.url").replace("%%", "%")
    kwargs = {}
    if url.startswith("sqlite") and (":memory:" in url or url == "sqlite://"):
        kwargs["poolclass"] = pool.StaticPool
    engine = create_engine(url, **kwargs)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True,
                          compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
