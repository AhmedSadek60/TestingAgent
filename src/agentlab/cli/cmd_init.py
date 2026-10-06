"""``agentlab init``: write a starting configuration and target definition. Existing files are never overwritten."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from agentlab.cli.common import console
from agentlab.cli.markup import esc

CONFIG = """\
# AgentLab configuration. Every key is optional; the values shown are the defaults that matter.
# Secrets never go in this file: provider keys are *references* (env:NAME or secret:NAME).

providers:
  # A built-in deterministic provider so everything works offline. It is not a real model.
  - name: mock
    type: mock
    model: mock-judge
  # A local model needs no key (Ollama shown; LM Studio, vLLM and llama.cpp are configured the same way):
  # - name: ollama
  #   type: ollama
  #   base_url: http://127.0.0.1:11434
  #   model: <a model you have pulled>
  # Hosted providers take a key reference:
  # - name: gemini
  #   type: gemini
  #   api_key_ref: env:GEMINI_API_KEY
  #   model: <model name>
  # - name: openrouter
  #   type: openrouter
  #   api_key_ref: env:OPENROUTER_API_KEY
  #   model: <model name>

evaluation:
  # LLM judges grade criteria no rule can check. Leave empty for deterministic checks only.
  # A judge is never allowed to be the model under test.
  judges: []
  # judges: [{provider: ollama, model: <model name>}]
  reliability_repetitions: 3

limits:
  max_cost_usd: 10.0
  max_execution_time_seconds: 3600

security:
  sandbox_required: true          # repositories and coding agents run only inside Docker, never on this machine
  allow_production_targets: false
  allow_private_networks: true    # needed to test agents on localhost or a private network

# Your own skills (agentlab skills new NAME) live here.
skill_dirs: [skills]
"""

TARGET = """\
# The agent to test. Give at least one way to reach or read it; more is better.
name: my-agent
description: What this agent is for, in a sentence or two (improves the plan).
objective: What a good result means for you.

# --- an HTTP endpoint -------------------------------------------------------------------------------------------
api:
  url: http://localhost:8000/chat
  method: POST
  request_template:                 # {{input}} and {{session_id}} are filled in by AgentLab
    input: "{{input}}"
    session_id: "{{session_id}}"
  response:
    output: $.output                # JSONPath to the agent's answer
  # auth_credential: test-user      # a credential stored with `agentlab credentials add`

# --- other ways in (uncomment what applies) ---------------------------------------------------------------------
# web: {url: http://localhost:3000}                                    # a chat UI, driven with a browser
# mcp: {transport: streamable_http, url: http://localhost:9000/mcp}    # an MCP server
# repository: {path: ../my-agent}                                      # source: analysed, executed only in Docker
# documents: [docs/policies.md]                                        # what the agent should know

# --- what you authorise ----------------------------------------------------------------------------------------
# Safe tests always run. Add controlled tests only for a target you own. High-impact tests also need a written
# authorization_note and, on a remote target, a disposable environment.
safety:
  production: false
  authorized_risk_classes: [safe, controlled]
  # disposable_environment: true
  # authorization_note: "Approved by <name> on <date>"

# Secrets you planted on purpose (in the system prompt or knowledge base) so leaks can be detected exactly:
# known_canaries: [AGENTLAB_CANARY_planted_value_1]
"""

SKILLS_README = """\
# Local skills

Skills here are loaded by AgentLab (see `skill_dirs` in agentlab.yaml). Create one with `agentlab skills new NAME`,
check it with `agentlab skills validate skills/NAME`, and try it with `agentlab test --skills NAME ...`.
A skill is a folder with `skill.yaml` (what to test) and `SKILL.md` (why and how). See docs/skills.md.
"""


def init(
    directory: Annotated[Path, typer.Argument(help="Where to write the files (default: the current folder).")] = Path(
        "."
    ),
    force: Annotated[bool, typer.Option("--force", help="Overwrite files that already exist.")] = False,
) -> None:
    """Create agentlab.yaml, target.yaml, a skills/ folder and the private .agentlab/ data folder."""
    directory.mkdir(parents=True, exist_ok=True)
    files = {
        directory / "agentlab.yaml": CONFIG,
        directory / "target.yaml": TARGET,
        directory / "skills" / "README.md": SKILLS_README,
        # keeps the database, artifacts and the encrypted secret store out of version control
        directory / ".agentlab" / ".gitignore": "*\n",
    }
    for path, text in files.items():
        if path.exists() and not force:
            console.print(f"[yellow]kept[/yellow]    {esc(path)} (exists; use --force to overwrite)")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        console.print(f"[green]created[/green] {esc(path)}")
    console.print(
        "\nNext: edit [bold]target.yaml[/bold], then `agentlab doctor`, `agentlab test --target target.yaml --plan-only`."
    )
    console.print("No agent yet? Try the built-in demo agent: `agentlab test --mock success --intensity quick`.")
