"""Option definitions shared by the commands that describe a target (``discover`` and ``test``)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

TARGET = "What to test"
AUTH = "Authorization (what the owner of the target allows)"

TargetFile = Annotated[
    Path | None,
    typer.Option(
        "--target", "-t", help="target.yaml describing the agent (flags below override it).", rich_help_panel=TARGET
    ),
]
Name = Annotated[
    str | None, typer.Option("--name", help="Name of the target (default: derived).", rich_help_panel=TARGET)
]
Repo = Annotated[
    str | None,
    typer.Option("--repo", help="https Git URL or local path of the agent's repository.", rich_help_panel=TARGET),
]
Ref = Annotated[str | None, typer.Option("--ref", help="Branch, tag or commit of --repo.", rich_help_panel=TARGET)]
WebUrl = Annotated[
    str | None, typer.Option("--url", help="Web UI of the agent (browser testing).", rich_help_panel=TARGET)
]
ApiUrl = Annotated[str | None, typer.Option("--api-url", help="HTTP endpoint of the agent.", rich_help_panel=TARGET)]
OpenApi = Annotated[
    str | None, typer.Option("--openapi", help="OpenAPI document URL of the agent's API.", rich_help_panel=TARGET)
]
McpUrl = Annotated[str | None, typer.Option("--mcp-url", help="MCP server (streamable HTTP).", rich_help_panel=TARGET)]
McpCommand = Annotated[
    str | None, typer.Option("--mcp-command", help="MCP server started over stdio.", rich_help_panel=TARGET)
]
Command = Annotated[
    str | None,
    typer.Option("--command", help="Command that runs the agent inside the sandbox.", rich_help_panel=TARGET),
]
Llm = Annotated[
    str | None,
    typer.Option(
        "--llm",
        help="Test a model as the agent: PROVIDER or PROVIDER:MODEL (e.g. ollama:qwen2.5:0.5b).",
        rich_help_panel=TARGET,
    ),
]
SystemPrompt = Annotated[
    str | None,
    typer.Option("--system-prompt", help="System prompt for --llm (text or @file).", rich_help_panel=TARGET),
]
Mock = Annotated[
    list[str] | None,
    typer.Option(
        "--mock",
        help="Built-in deterministic agent for demos and self-tests; repeat for behaviours "
        "(success, hallucination, prompt_injection, unsafe_behavior, flaky, ...).",
        rich_help_panel=TARGET,
    ),
]
Docs = Annotated[
    list[str] | None,
    typer.Option("--docs", help="Document or folder describing the agent (repeatable).", rich_help_panel=TARGET),
]
Description = Annotated[
    str | None, typer.Option("--description", help="What the agent is for, in your words.", rich_help_panel=TARGET)
]
Objective = Annotated[
    str | None, typer.Option("--objective", help="What a good result means for you.", rich_help_panel=TARGET)
]
Credentials = Annotated[
    list[str] | None,
    typer.Option(
        "--credentials",
        "-C",
        help="Name of a stored test credential (agentlab credentials add).",
        rich_help_panel=TARGET,
    ),
]
Authorize = Annotated[
    list[str] | None,
    typer.Option(
        "--authorize",
        help="Allow a risk class on this target: controlled | high_impact (repeatable). Safe tests always run. "
        "high_impact tests also need --authorization-note, and on a remote target --disposable-environment.",
        rich_help_panel=AUTH,
    ),
]
AuthorizationNote = Annotated[
    str | None,
    typer.Option(
        "--authorization-note",
        help="Who authorised the testing and why, in your words. Needed for high_impact tests and for security tests "
        "against a remote host; kept with the target.",
        rich_help_panel=AUTH,
    ),
]
Disposable = Annotated[
    bool,
    typer.Option(
        "--disposable-environment",
        help="The target is a disposable test deployment (a remote target needs this for high_impact tests).",
        rich_help_panel=AUTH,
    ),
]
Production = Annotated[
    bool,
    typer.Option(
        "--production",
        help="The target is a production system: only safe tests run, and high_impact tests never do.",
        rich_help_panel=AUTH,
    ),
]
