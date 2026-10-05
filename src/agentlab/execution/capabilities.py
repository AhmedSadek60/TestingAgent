"""What a test needs *from the environment* before it can run (spec sections 16 and 35).

A test that must plant something in the target (a canary, a document, a poisoned tool result) or needs something
AgentLab itself must provide (an isolated workspace, a local instrumented site) is **BLOCKED** when that is not
possible: it is never simulated and never counted as a failure. The executor applies these rules when it runs a
test and the designer applies the very same rules when it predicts blocking, so a plan and its run agree.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from agentlab.adapters.base import AdapterCapabilities

# capabilities that the adapter reports about the *interface*
INTERFACE_CAPABILITIES = (
    "canary_seeding",
    "knowledge_injection",
    "tool_output_injection",
    "multimodal",
    "attachments",
    "omit_auth",
)
# capabilities that AgentLab's *environment* provides, independent of the interface
ENVIRONMENT_CAPABILITIES = ("workspace", "local_site")

HINTS = {
    "canary_seeding": "AgentLab cannot place a secret in this target's hidden instructions; declare "
    "`known_canaries` in target.yaml for a deployment you configured with synthetic secrets",
    "knowledge_injection": "AgentLab cannot add documents to this target's knowledge for a single session",
    "tool_output_injection": "AgentLab cannot replace a tool result of this target with test content",
    "multimodal": "this interface cannot pass images/attachments to the model",
    "attachments": "this interface does not accept file attachments",
    "omit_auth": "this interface cannot send a request without its credentials, so enforcement cannot be checked",
    "workspace": "an isolated Docker workspace is required and Docker is unavailable (AgentLab never runs on the host)",
    "local_site": "the instrumented local site is only reachable by a target on this machine or private network and "
    "needs an installed browser engine",
}


def missing_capabilities(
    needs: Iterable[str],
    caps: AdapterCapabilities,
    *,
    known_canaries: bool = False,
    environment: Mapping[str, bool] | None = None,
) -> list[str]:
    """The capabilities in ``needs`` that this interface / environment cannot provide."""
    env = environment or {}
    out: list[str] = []
    for need in needs:
        if need == "canary_seeding" and known_canaries:
            continue  # the owner planted the secrets in the target; AgentLab only has to look for them
        if need in ENVIRONMENT_CAPABILITIES:
            if not env.get(need, False):
                out.append(need)
        elif not getattr(caps, need, False):
            out.append(need)
    return out


def describe_missing(kind: str, missing: list[str]) -> str:
    why = "; ".join(HINTS.get(m, m) for m in missing)
    return f"interface '{kind}' lacks capability {missing}: {why}"
