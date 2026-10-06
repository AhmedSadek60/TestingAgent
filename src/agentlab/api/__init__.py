"""AgentLab's REST API and web interface host (spec sections 27 and 41)."""

from __future__ import annotations

from typing import Any

__all__ = ["create_app"]


def create_app(*args: Any, **kwargs: Any) -> Any:
    """Build the FastAPI application (see :func:`agentlab.api.app.create_app`). Imported lazily so that using the command
    line does not load the web framework."""
    from agentlab.api.app import create_app as _create

    return _create(*args, **kwargs)
