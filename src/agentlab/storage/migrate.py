"""Programmatic Alembic helpers (the migration scripts ship inside the package)."""

from __future__ import annotations

import threading
from pathlib import Path

from alembic import command
from alembic.config import Config

# Alembic keeps its environment in module-level proxies, so two migrations in one process cannot overlap (a server
# opening several databases at once, a thread pool of self-tests). One at a time is cheap and always correct.
_MIGRATING = threading.Lock()


def _config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def upgrade(url: str, revision: str = "head") -> None:
    with _MIGRATING:
        command.upgrade(_config(url), revision)


def downgrade(url: str, revision: str) -> None:
    with _MIGRATING:
        command.downgrade(_config(url), revision)


def current_head() -> str:
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(_config("sqlite://")).get_current_head() or ""
