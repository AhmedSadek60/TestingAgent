"""The adapter for tests that never talk to the target.

A static test inspects what AgentLab already knows (the discovered tools, the repository analysis) and needs no live
interface. It still has to run *somewhere*, so the executor hands it this adapter. It cannot be configured from a
target file and refuses every request: a test that reaches ``send`` has been routed to the wrong engine.
"""

from __future__ import annotations

from agentlab.adapters.base import AdapterCapabilities, AdapterContext, AgentAdapter
from agentlab.core.errors import UnsupportedCapability
from agentlab.core.models import AgentRequest, AgentResponse, TargetSpec


class NullAdapter(AgentAdapter):
    kind = "none"

    def __init__(self, spec: TargetSpec, ctx: AdapterContext) -> None:
        super().__init__(spec, ctx)
        self.capabilities = AdapterCapabilities(
            conversational=False,
            sessions=False,
            notes=["no interface: only tests that inspect discovered information can run"],
        )

    async def send(self, request: AgentRequest) -> AgentResponse:
        raise UnsupportedCapability("this test has no live interface to send a request to")
