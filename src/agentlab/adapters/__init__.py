"""Target agent adapters. Importing this package registers the built-in adapters."""

from agentlab.adapters import command, http, llm, mcp, mock  # noqa: F401  (registration side effects)
from agentlab.adapters.base import (
    ADAPTERS,
    AdapterCapabilities,
    AdapterContext,
    AgentAdapter,
    TargetRuntime,
    create_adapter,
)

__all__ = ["ADAPTERS", "AdapterCapabilities", "AdapterContext", "AgentAdapter", "TargetRuntime", "create_adapter"]
