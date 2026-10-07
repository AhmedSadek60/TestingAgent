"""Every plug-in registry in one place (spec section 43).

``agentlab plugins`` lists them and the tests walk them; nothing else needs to know how many there are. Importing a
registry's module registers the built-in plug-ins it ships, so asking for the registries loads every one of them.
"""

from __future__ import annotations

from typing import Any

from agentlab.core.plugins import Registry


def all_registries() -> dict[str, Registry[Any]]:
    """Registry by kind. The kind is also the entry-point group's suffix: ``providers`` is ``agentlab.providers``."""
    from agentlab.adapters import ADAPTERS
    from agentlab.documents.parsers import DOCUMENT_PARSERS
    from agentlab.evaluation.assertions import ASSERTIONS
    from agentlab.execution import (
        executor as _executor,  # noqa: F401 - registers the browser, load, site and workspace engines
    )
    from agentlab.execution.engines import ENGINES
    from agentlab.providers.registry import PROVIDER_TYPES
    from agentlab.reporting.renderers import REPORT_RENDERERS
    from agentlab.sandbox import SANDBOX_PROVIDERS
    from agentlab.storage.artifacts import ARTIFACT_STORES
    from agentlab.storage.vectors import VECTOR_STORES

    return {
        "providers": PROVIDER_TYPES,
        "adapters": ADAPTERS,
        "engines": ENGINES,
        "sandbox": SANDBOX_PROVIDERS,
        "assertions": ASSERTIONS,
        "artifact_stores": ARTIFACT_STORES,
        "vector_stores": VECTOR_STORES,
        "document_parsers": DOCUMENT_PARSERS,
        "report_renderers": REPORT_RENDERERS,
    }


def origin(item: object) -> str:
    """Where a registered plug-in is defined: its module. AgentLab's own plug-ins live in the ``agentlab`` package;
    anything else came from a package or module the owner installed or named in ``plugins:``."""
    module = getattr(item, "__module__", None) or type(item).__module__
    return str(module)


def is_builtin(item: object) -> bool:
    module = origin(item)
    return module == "agentlab" or module.startswith("agentlab.")


__all__ = ["all_registries", "is_builtin", "origin"]
