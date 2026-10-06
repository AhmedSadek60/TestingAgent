"""Disposable fixture agents with planted defects (spec section 23)."""

from __future__ import annotations

from agentlab.fixtures.base import PLANTED_CANARY, ChatRequest, FixtureAgent, Reply, Session, make_app
from agentlab.fixtures.chatbot import ChatbotAgent
from agentlab.fixtures.document_agent import DocumentAgent
from agentlab.fixtures.mcp_agent import McpToolServer
from agentlab.fixtures.memory_agent import MemoryAgent
from agentlab.fixtures.multi_agent import TeamAgent
from agentlab.fixtures.planning_agent import PlanningAgent
from agentlab.fixtures.rag_agent import RagAgent
from agentlab.fixtures.tool_agent import ToolAgent
from agentlab.fixtures.unreliable import UnreliableAgent
from agentlab.fixtures.vulnerable import VulnerableAgent

REGISTRY: dict[str, type[FixtureAgent]] = {
    c.kind: c
    for c in (
        ChatbotAgent,
        DocumentAgent,
        McpToolServer,
        MemoryAgent,
        PlanningAgent,
        RagAgent,
        TeamAgent,
        ToolAgent,
        UnreliableAgent,
        VulnerableAgent,
    )
}


def fixture_class(kind: str) -> type[FixtureAgent]:
    try:
        return REGISTRY[kind]
    except KeyError:
        raise ValueError(f"unknown fixture '{kind}'; available: {', '.join(sorted(REGISTRY))}") from None


__all__ = [
    "PLANTED_CANARY",
    "REGISTRY",
    "ChatRequest",
    "ChatbotAgent",
    "DocumentAgent",
    "FixtureAgent",
    "McpToolServer",
    "MemoryAgent",
    "PlanningAgent",
    "RagAgent",
    "Reply",
    "Session",
    "TeamAgent",
    "ToolAgent",
    "UnreliableAgent",
    "VulnerableAgent",
    "fixture_class",
    "make_app",
]
