"""``agentlab skills``: list, inspect, validate, create, import, forge and promote test skills."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich import box
from rich.markdown import Markdown
from rich.table import Table

from agentlab.cli import options as o
from agentlab.cli.common import console, emit_json, err, fail, load_config, run_async, state
from agentlab.cli.markup import esc, styled
from agentlab.cli.targets import build_target
from agentlab.core.config import AgentLabConfig
from agentlab.core.errors import UserError
from agentlab.security import safeyaml
from agentlab.services import load_plugin_modules
from agentlab.skills import SkillRegistry
from agentlab.skills.forge import forge_skill, suggest_methodology, uncovered_capabilities
from agentlab.skills.importer import import_skill, promote_skill
from agentlab.skills.loader import discover_skill_dirs, load_skill_dir
from agentlab.skills.model import NAME, REQUIRED_DOC_SECTIONS

skills_app = typer.Typer(
    help="The test skills AgentLab uses: list, show, create, import and promote.", no_args_is_help=True
)

TRUST_STYLE = {
    "builtin": "green",
    "plugin": "cyan",
    "local": "blue",
    "imported": "yellow",
    "generated": "yellow",
}


def drafts_dir(cfg: AgentLabConfig, base: Path) -> Path:
    """Where imported and generated skills wait, untrusted, for a human to promote them (storage.skill_drafts_dir)."""
    path = Path(cfg.storage.skill_drafts_dir)
    return path if path.is_absolute() else base / path


def local_dir(cfg: AgentLabConfig, base: Path) -> Path:
    """Where the owner's own skills live: the first ``skill_dirs`` entry, else ``./skills``."""
    if cfg.skill_dirs:
        p = Path(cfg.skill_dirs[0])
        return p if p.is_absolute() else base / p
    return base / "skills"


def _registry(ctx: typer.Context, *, with_drafts: bool = False) -> tuple[SkillRegistry, AgentLabConfig, Path]:
    cfg, base = load_config(state(ctx))
    dirs = [d if Path(d).is_absolute() else str(base / d) for d in cfg.skill_dirs]
    reg = SkillRegistry.default(dirs)
    if with_drafts and drafts_dir(cfg, base).is_dir():
        for d in discover_skill_dirs(drafts_dir(cfg, base)):
            reg.add(load_skill_dir(d, trust=_draft_trust(d)))
    return reg, cfg, base


def _draft_trust(path: Path) -> str:
    try:
        origin = (safeyaml.load((path / "skill.yaml").read_text(encoding="utf-8")) or {}).get("provenance", {})
    except Exception:
        return "imported"
    return "generated" if str(origin.get("origin", "")) == "agentlab-skillforge" else "imported"


@skills_app.command("list")
def skills_list(
    ctx: typer.Context,
    drafts: Annotated[bool, typer.Option("--drafts", help="Also list imported and generated drafts.")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """List the skills, with their version, origin, trust level and the taxonomy letters they cover."""
    reg, _, _ = _registry(ctx, with_drafts=drafts)
    skills = [s for s in reg.all() if drafts or not s.draft]
    if as_json:
        emit_json(
            {
                "skills": [
                    {
                        "name": s.name,
                        "version": s.version,
                        "trust": s.manifest.trust,
                        "status": s.manifest.status,
                        "kind": s.manifest.kind,
                        "taxonomy": s.manifest.taxonomy,
                        "risk_class": s.manifest.risk_class.value,
                        "title": s.manifest.title,
                        "content_hash": s.content_hash,
                        "problems": s.problems,
                    }
                    for s in skills
                ],
                "problems": reg.problems,
            }
        )
        return
    t = Table("Skill", "Version", "Trust", "Status", "Covers", "Risk", "Title", box=box.SIMPLE_HEAD)
    for s in skills:
        m = s.manifest
        mark = " [red]![/red]" if s.problems else ""
        t.add_row(
            esc(s.name) + mark,
            esc(m.version),
            styled(m.trust, TRUST_STYLE.get(m.trust)),
            esc(m.status),
            esc("".join(m.taxonomy) or "-"),
            esc(m.risk_class.value),
            esc(m.title),
        )
    console.print(t)
    console.print(f"[dim]{len(skills)} skills. `agentlab skills show NAME` explains one.[/dim]")
    for p in reg.problems:
        console.print(f"[yellow]problem[/yellow] {esc(p)}")


@skills_app.command("show")
def skills_show(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Skill name.")],
    as_json: Annotated[bool, typer.Option("--json", help="Print the manifest as JSON.")] = False,
) -> None:
    """Show a skill: when it applies, what it needs, how it tests and what it cannot do."""
    reg, _, _ = _registry(ctx, with_drafts=True)
    skill = reg.get(name)
    if as_json:
        emit_json({"manifest": skill.manifest.model_dump(mode="json"), "problems": skill.problems, "doc": skill.doc})
        return
    m = skill.manifest
    console.print(
        f"[bold]{esc(m.title)}[/bold]  [dim]{esc(m.name)} {esc(m.version)} · {esc(m.trust)} · {esc(m.status)} · "
        f"risk {m.risk_class.value} · hash {skill.content_hash[:12]}[/dim]"
    )
    console.print(esc(m.description))
    a = m.applicability
    applies = [
        *(["every target"] if a.always else []),
        *(f"type {t}" for t in a.types),
        *(f"capability {c}" for c in a.capabilities),
        *(f"interface {i}" for i in a.interfaces),
        *(f"tools matching {p}" for p in a.tool_patterns),
    ]
    console.print(
        "[bold]Applies to[/bold] " + (esc(", ".join(applies)) or "nothing by itself (select it with --skills)")
    )
    needs = [
        n
        for n, on in (
            ("credentials", m.prerequisites.credentials),
            ("docker", m.prerequisites.docker),
            ("browser", m.prerequisites.browser),
            ("an LLM judge", m.prerequisites.judge),
            ("a multimodal model", m.prerequisites.multimodal),
        )
        if on
    ]
    console.print("[bold]Needs[/bold] " + (", ".join(needs) or "nothing beyond a reachable target"))
    if m.limitations:
        console.print("[bold]Limitations[/bold]")
        for line in m.limitations:
            console.print(f"  - {esc(line)}")
    for p in skill.problems:
        console.print(f"[red]problem[/red] {esc(p)}")
    if skill.doc:
        console.print(Markdown(skill.doc))


@skills_app.command("validate")
def skills_validate(
    ctx: typer.Context,
    path: Annotated[Path, typer.Argument(help="A skill directory, or a folder of skill directories.")],
) -> None:
    """Check skills for problems the loader would reject: the manifest, SKILL.md, and every test a template describes."""
    cfg, _ = load_config(state(ctx))
    for warning in load_plugin_modules(cfg.plugins):  # plug-ins may add assertion kinds a skill uses
        err.print(f"warning: {warning}", markup=False)
    dirs = discover_skill_dirs(path)
    if not dirs:
        raise UserError(f"no skill (a folder with skill.yaml) found under {path}")
    bad = 0
    for d in dirs:
        skill = load_skill_dir(d, trust="local")
        if skill.problems:
            bad += 1
            console.print(f"[red]x[/red] {esc(d.name)}")
            for p in skill.problems:
                console.print(f"    {esc(p)}")
        else:
            console.print(f"[green]ok[/green] {esc(skill.name)} {esc(skill.version)} ({skill.content_hash[:12]})")
    if bad:
        raise typer.Exit(2)


SCAFFOLD_MANIFEST = """\
name: {name}
version: 0.1.0
title: {title}
description: "One sentence: what this skill tests and why it matters."
kind: tests
status: experimental
taxonomy: [A]                  # spec taxonomy letters this skill covers
category: functional
score_category: functional_quality
id_prefix: {prefix}
risk_class: safe               # safe | controlled | high_impact (the gate decides whether it may run)
applicability:                 # when AgentLab selects this skill by itself; empty = only with --skills {name}
  types: []                    # e.g. [rag, tool_calling]
  capabilities: []
  tool_patterns: []
templates:
  - id: EXAMPLE
    test:
      name: Example check
      objective: Replace with the behaviour this test proves.
      input: Replace with the message sent to the agent.
      assertions:
        - {{type: not_empty}}
        - {{type: no_error}}
      severity_on_failure: low
    reasons:
      - Replace with why this test exists.
limitations:
  - Replace with what this skill does NOT cover.
provenance:
  origin: local
  license: Apache-2.0
"""


@skills_app.command("new")
def skills_new(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Lowercase words joined by '-', e.g. refund-policy.")],
    to: Annotated[
        Path | None, typer.Option("--to", help="Skills folder (default: the first skill_dirs entry).")
    ] = None,
) -> None:
    """Create a skill folder to edit: a valid manifest, a template test and a SKILL.md with every required section."""
    if not NAME.match(name):
        raise UserError("skill names are lowercase words joined by '-', e.g. refund-policy")
    cfg, base = load_config(state(ctx))
    dest = (to or local_dir(cfg, base)) / name
    if dest.exists():
        raise UserError(f"{dest} already exists")
    dest.mkdir(parents=True)
    prefix = "".join(w[0] for w in name.split("-")[:3]).upper()
    (dest / "skill.yaml").write_text(
        SCAFFOLD_MANIFEST.format(name=name, title=name.replace("-", " ").title(), prefix=prefix), encoding="utf-8"
    )
    prompts = {
        "Purpose": "Why this behaviour matters to the people who use the agent.",
        "Applicability": "When the skill applies and when it does not.",
        "Prerequisites": "Interfaces, credentials, tools or documents the tests need.",
        "Methodology": "How the behaviour is tested and why the oracle is trustworthy.",
        "Test generation": "How tests are derived from the target (templates, tools, documents).",
        "Execution": "Isolation, risk class and what the test changes in the target, if anything.",
        "Evaluation rules": "Deterministic checks first; judge criteria only when no oracle exists.",
        "Severity guidance": "Which failures are critical, high, medium or low, with reasoning.",
        "Evidence requirements": "What a reviewer needs to reproduce a finding.",
    }
    body = "\n".join(f"## {s}\n\n{prompts[s]}\n" for s in REQUIRED_DOC_SECTIONS)
    (dest / "SKILL.md").write_text(f"# {name.replace('-', ' ').title()}\n\n{body}", encoding="utf-8")
    problems = load_skill_dir(dest, trust="local").problems
    console.print(
        f"created [bold]{esc(dest)}[/bold]" + (f" [yellow]({len(problems)} problem(s))[/yellow]" if problems else "")
    )
    if str(dest.parent.resolve()) not in {str((base / d).resolve()) for d in cfg.skill_dirs}:
        console.print(f"[yellow]add `{esc(dest.parent)}` to skill_dirs in agentlab.yaml so AgentLab loads it[/yellow]")
    console.print(f"edit it, then `agentlab skills validate {esc(dest)}` and `agentlab test --skills {esc(name)} ...`")


@skills_app.command("import")
def skills_import(
    ctx: typer.Context,
    source: Annotated[str, typer.Argument(help="A skill file or folder (e.g. a downloaded SKILL.md).")],
    name: Annotated[str | None, typer.Option("--name", help="Name for the draft.")] = None,
    to: Annotated[Path | None, typer.Option("--to", help="Drafts folder.")] = None,
) -> None:
    """Import a third-party skill as an UNTRUSTED DRAFT: scanned, secrets redacted, nothing in it is ever executed."""
    cfg, base = load_config(state(ctx))
    report = import_skill(source, to or drafts_dir(cfg, base), name=name)
    if report.blocked:
        raise fail(f"nothing imported: {report.block_reason}")
    console.print(
        f"imported [bold]{esc(report.name)}[/bold] -> {esc(report.destination)} (license: {esc(report.license)})"
    )
    console.print(
        "[yellow]This is an untrusted draft.[/yellow] It is never selected until a human adapts it and runs "
        f"`agentlab skills promote {esc(report.destination)} --reviewer YOU`."
    )
    for w in report.warnings:
        console.print(f"[yellow]warning[/yellow] {esc(w)}")


@skills_app.command("forge")
def skills_forge(
    ctx: typer.Context,
    target: o.TargetFile = None,
    name: o.Name = None,
    repo: o.Repo = None,
    url: o.WebUrl = None,
    api_url: o.ApiUrl = None,
    llm: o.Llm = None,
    mock: o.Mock = None,
    docs: o.Docs = None,
    description: o.Description = None,
    to: Annotated[Path | None, typer.Option("--to", help="Drafts folder.")] = None,
    suggest: Annotated[
        bool,
        typer.Option(
            "--suggest",
            help="Ask the configured LLM provider for methodology ideas (sends the capability name and a short evidence "
            "excerpt to that provider; off by default).",
        ),
    ] = False,
) -> None:
    """Draft skills for capabilities discovery found but no installed skill covers (e.g. an unusual agent type)."""
    from agentlab.discovery.agent import TargetDiscoveryAgent

    spec = build_target(
        target_file=target,
        name=name,
        repo=repo,
        url=url,
        api_url=api_url,
        llm=llm,
        mock=mock,
        docs=docs,
        description=description,
    )
    reg, cfg, base = _registry(ctx)
    from agentlab.cli.common import make_services

    services = make_services(state(ctx), migrate=False)

    async def go() -> list[Path]:
        try:
            agent = TargetDiscoveryAgent(
                cfg,
                providers=services.providers,
                credentials=services.credentials,
                artifacts=services.artifacts,
                sandbox=services.sandbox,
            )
            profile = (await agent.discover(spec)).profile
            gaps = uncovered_capabilities(profile, reg)
            made: list[Path] = []
            for cap in gaps:
                suggestion, label = (None, None)
                if suggest:
                    judge = (
                        (cfg.evaluation.judges[0].provider, cfg.evaluation.judges[0].model)
                        if cfg.evaluation.judges
                        else None
                    )
                    suggestion, label = await suggest_methodology(services.providers, cap, profile, judge)
                made.append(forge_skill(cap, to or drafts_dir(cfg, base), suggestion=suggestion, provider_label=label))
            return made
        finally:
            await services.aclose()

    made = run_async(go())
    if not made:
        console.print("every detected capability is already covered by an installed skill; nothing to forge")
        return
    for d in made:
        console.print(f"drafted [bold]{esc(d.name)}[/bold] -> {esc(d)}")
    err.print("[yellow]Generated drafts only cover a smoke check and are never selected until promoted.[/yellow]")


@skills_app.command("promote")
def skills_promote(
    ctx: typer.Context,
    draft: Annotated[Path, typer.Argument(help="The reviewed draft folder.")],
    reviewer: Annotated[str, typer.Option("--reviewer", help="Who reviewed it (recorded in the skill).")],
    to: Annotated[Path | None, typer.Option("--to", help="Skills folder (default: first skill_dirs entry).")] = None,
) -> None:
    """Move a reviewed draft into the local skills folder, where AgentLab can select it."""
    cfg, base = load_config(state(ctx))
    dest = promote_skill(draft, to or local_dir(cfg, base), reviewer=reviewer)
    console.print(f"promoted -> {esc(dest)}")
    if not cfg.skill_dirs:
        console.print("[yellow]add the folder to skill_dirs in agentlab.yaml so AgentLab loads it[/yellow]")
