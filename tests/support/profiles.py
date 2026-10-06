"""Hand-built target profiles and skill contexts so skill tests need no network and no discovery run."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentlab.adapters.base import AdapterCapabilities
from agentlab.core.config import AgentLabConfig
from agentlab.core.enums import AgentType, Support
from agentlab.core.models import AgentProfile, CapabilityEntry, TargetSpec, ToolInfo, TypeScore
from agentlab.skills import SkillContext

FULL_CAPS = AdapterCapabilities(
    reports_tool_calls=True,
    reports_contexts=True,
    reports_events=True,
    canary_seeding=True,
    knowledge_injection=True,
    tool_output_injection=True,
    attachments=True,
    multimodal=True,
)

TOOLS = [
    ToolInfo(
        name="get_weather",
        description="Get the current weather for a city",
        parameters={"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
        side_effects="read",
    ),
    ToolInfo(
        name="send_email",
        description="Send an email to a recipient",
        parameters={
            "type": "object",
            "properties": {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}},
            "required": ["to", "subject"],
        },
        side_effects="external",
    ),
    ToolInfo(
        name="delete_file",
        description="Delete a file by path",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        side_effects="destructive",
    ),
    ToolInfo(
        name="calculator",
        description="Evaluate an arithmetic expression",
        parameters={"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]},
        side_effects="none",
    ),
]


def make_profile(types: dict[str, float] | None = None, tools: list[ToolInfo] | None = None, **kw: Any) -> AgentProfile:
    types = types or {"chatbot": 0.8, "tool_calling": 0.9}
    return AgentProfile(
        target_name=kw.pop("target_name", "fixture"),
        types=[TypeScore(type=AgentType(t), confidence=c) for t, c in types.items()],
        interfaces=kw.pop("interfaces", ["mock"]),
        tools=list(TOOLS if tools is None else tools),
        capability_matrix=[
            CapabilityEntry(capability=t, detected=True, testable=Support.SUPPORTED) for t in types if t != "chatbot"
        ],
        **kw,
    )


def make_ctx(
    types: dict[str, float] | None = None,
    *,
    tools: list[ToolInfo] | None = None,
    caps: AdapterCapabilities | None = None,
    intensity: str = "standard",
    interfaces: list[str] | None = None,
    fixtures_dir: Path | None = None,
    **kw: Any,
) -> SkillContext:
    profile = make_profile(types, tools, interfaces=interfaces or ["mock"])
    return SkillContext(
        profile=profile,
        target=kw.pop("target", TargetSpec(name="fixture")),
        config=AgentLabConfig(),
        interfaces=profile.interfaces,
        adapter_capabilities={profile.interfaces[0]: caps or FULL_CAPS},
        intensity=intensity,
        fixtures_dir=fixtures_dir,
        **kw,
    )
