"""Typed output of the RepositoryAnalyzer. Every item carries provenance (path:line)."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from agentlab.core.models import ArchitectureGraph
from agentlab.core.models.base import Model


class Located(Model):
    path: str
    line: int | None = None

    def ref(self) -> str:
        return f"{self.path}:{self.line}" if self.line else self.path


class DependencyInfo(Model):
    name: str
    version: str | None = None
    ecosystem: str
    source: str
    dev: bool = False  # development/test-only: never used to infer what the agent *does*


class ToolDefinition(Model):
    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    framework: str = "unknown"
    location: Located
    side_effects: str = "unknown"
    requires_confirmation: bool | None = None


class PromptItem(Model):
    kind: str  # system_prompt | instructions | prompt_file | agent_guidance
    excerpt: str
    sha256: str
    location: Located
    injection_indicators: list[str] = Field(default_factory=list)
    chars: int = 0


class AgentDefinition(Model):
    name: str
    framework: str
    location: Located
    role: str | None = None
    tools: list[str] = Field(default_factory=list)


class McpServerConfig(Model):
    name: str
    transport: str = "unknown"
    command: str | None = None
    url: str | None = None
    location: Located
    role: str = "client"  # client: the agent consumes it | server: the repo implements it
    env_keys: list[str] = Field(default_factory=list)


class ApiRoute(Model):
    method: str
    path: str
    location: Located
    framework: str = "unknown"


class Signal(Model):
    kind: str
    detail: str
    location: Located | None = None
    weight: float = 0.5


class RepositoryAnalysis(Model):
    root_name: str
    commit: str | None = None
    ref: str | None = None
    url: str | None = None
    file_count: int = 0
    total_bytes: int = 0
    skipped: dict[str, int] = Field(default_factory=dict)
    languages: dict[str, int] = Field(default_factory=dict)  # language -> bytes
    frameworks: list[str] = Field(default_factory=list)
    dependencies: list[DependencyInfo] = Field(default_factory=list)
    entry_points: list[Located] = Field(default_factory=list)
    agent_definitions: list[AgentDefinition] = Field(default_factory=list)
    model_providers: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    tools: list[ToolDefinition] = Field(default_factory=list)
    prompts: list[PromptItem] = Field(default_factory=list)
    skills: list[Located] = Field(default_factory=list)
    mcp: list[McpServerConfig] = Field(default_factory=list)
    apis: list[ApiRoute] = Field(default_factory=list)
    openapi_specs: list[Located] = Field(default_factory=list)
    databases: list[str] = Field(default_factory=list)
    vector_databases: list[str] = Field(default_factory=list)
    memory_systems: list[str] = Field(default_factory=list)
    browser_frameworks: list[str] = Field(default_factory=list)
    tests: list[Located] = Field(default_factory=list)
    deployment: list[str] = Field(default_factory=list)
    env_requirements: list[str] = Field(default_factory=list)
    guidance_files: list[Located] = Field(default_factory=list)
    signals: list[Signal] = Field(default_factory=list)
    capabilities: dict[str, bool] = Field(default_factory=dict)
    architecture: ArchitectureGraph = Field(default_factory=ArchitectureGraph)
    warnings: list[str] = Field(default_factory=list)
    secrets_found: list[Located] = Field(default_factory=list)

    def has(self, cap: str) -> bool:
        return bool(self.capabilities.get(cap))
