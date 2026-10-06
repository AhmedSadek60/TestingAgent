"""``agentlab discover`` and ``agentlab test``: the two commands that point AgentLab at a target."""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
from pathlib import Path
from typing import Annotated, Any

import typer

from agentlab.cli import options as o
from agentlab.cli.cmd_runs import resolve_run_id
from agentlab.cli.common import (
    EXIT_FINDINGS,
    EXIT_INCOMPLETE,
    EXIT_NOT_TESTED,
    EXIT_OK,
    console,
    emit_json,
    err,
    make_services,
    run_async,
    state,
)
from agentlab.cli.progress import ProgressPrinter
from agentlab.cli.render import render_outcome, render_plan, render_profile
from agentlab.cli.targets import build_target
from agentlab.core.enums import RunStatus, Severity
from agentlab.core.errors import UserError
from agentlab.core.ids import new_id
from agentlab.core.models import TargetSpec
from agentlab.design import SUITES
from agentlab.design.render import plan_markdown
from agentlab.discovery.agent import DiscoveryResult
from agentlab.orchestrator import RunOptions, TestOrchestratorAgent
from agentlab.orchestrator.options import PreparedRun, RunOutcome
from agentlab.services import Services
from agentlab.skills.context import INTENSITIES
from agentlab.tracing import EventBus

FAIL_ON = ("none", "low", "medium", "high", "critical")
RUN = "Run"
LIMITS = "Limits"
OUT = "Output"


def exit_code(outcome: RunOutcome, fail_on: str) -> int:
    """The documented exit-code contract (see ``agentlab.cli.common``)."""
    if not outcome.tested:
        return EXIT_NOT_TESTED
    if fail_on != "none":
        threshold = Severity(fail_on).rank
        if any(f.severity.rank >= threshold for f in outcome.findings):
            return EXIT_FINDINGS
    return EXIT_OK if outcome.status == RunStatus.COMPLETED else EXIT_INCOMPLETE


# ====================================================================================================== discover
def discover(
    ctx: typer.Context,
    target: o.TargetFile = None,
    name: o.Name = None,
    repo: o.Repo = None,
    ref: o.Ref = None,
    url: o.WebUrl = None,
    api_url: o.ApiUrl = None,
    openapi: o.OpenApi = None,
    mcp_url: o.McpUrl = None,
    mcp_command: o.McpCommand = None,
    command: o.Command = None,
    llm: o.Llm = None,
    system_prompt: o.SystemPrompt = None,
    mock: o.Mock = None,
    docs: o.Docs = None,
    description: o.Description = None,
    objective: o.Objective = None,
    credentials: o.Credentials = None,
    no_probe: Annotated[
        bool,
        typer.Option(
            "--no-probe",
            help="Send the target no probe questions. An MCP server's tool list and a web page are still read, "
            "because that is how those are discovered.",
        ),
    ] = False,
    save: Annotated[Path | None, typer.Option("--save", help="Also write the profile as JSON to this file.")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print the profile as JSON.")] = False,
) -> None:
    """Fingerprint a target: what kind of agent it is, its tools, interfaces and risks. Sends only harmless probes."""
    st = state(ctx)
    spec = build_target(
        target_file=target,
        name=name,
        repo=repo,
        ref=ref,
        url=url,
        api_url=api_url,
        openapi=openapi,
        mcp_url=mcp_url,
        mcp_command=mcp_command,
        command=command,
        llm=llm,
        system_prompt=system_prompt,
        docs=docs,
        description=description,
        objective=objective,
        credentials=credentials,
        mock=mock,
    )
    _require_something(spec)
    services = make_services(st, migrate=False)
    result = run_async(_discover(services, spec, probe=not no_probe))
    profile = result.profile
    if save:
        save.parent.mkdir(parents=True, exist_ok=True)
        save.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
    if as_json:
        emit_json({"profile": profile.model_dump(mode="json"), "warnings": result.warnings})
        return
    render_profile(console, profile, result.warnings)
    if save:
        console.print(f"[dim]profile written to {save}[/dim]")


async def _discover(services: Services, spec: TargetSpec, *, probe: bool) -> DiscoveryResult:
    try:
        result: DiscoveryResult = await services.discover(spec, probe=probe)
        return result
    finally:
        await services.aclose()


def _require_something(spec: TargetSpec) -> None:
    if not (spec.interfaces() or spec.repository or spec.documents or spec.description):
        raise UserError(
            "nothing to test: give --target FILE, or at least one of --repo, --url, --api-url, --openapi, --mcp-url, "
            "--mcp-command, --command, --llm, --docs or --description (run `agentlab init` for a template)"
        )


def report_formats(values: list[str] | None) -> list[str] | None:
    """``--report``: ``None`` keeps the configured formats, ``none`` writes no report, anything else is validated."""
    if not values:
        return None
    words = {w.lower() for v in values for w in v.replace(",", " ").split()}
    if "none" in words:
        if len(words) > 1:
            raise UserError("--report none cannot be combined with other formats")
        return []
    from agentlab.reporting.bundle import normalise_formats

    return normalise_formats(values)


# ========================================================================================================== test
def test(
    ctx: typer.Context,
    target: o.TargetFile = None,
    name: o.Name = None,
    repo: o.Repo = None,
    ref: o.Ref = None,
    url: o.WebUrl = None,
    api_url: o.ApiUrl = None,
    openapi: o.OpenApi = None,
    mcp_url: o.McpUrl = None,
    mcp_command: o.McpCommand = None,
    command: o.Command = None,
    llm: o.Llm = None,
    system_prompt: o.SystemPrompt = None,
    mock: o.Mock = None,
    docs: o.Docs = None,
    description: o.Description = None,
    objective: o.Objective = None,
    credentials: o.Credentials = None,
    authorize: o.Authorize = None,
    authorization_note: o.AuthorizationNote = None,
    disposable_environment: o.Disposable = False,
    production: o.Production = False,
    suite: Annotated[
        str,
        typer.Option(
            "--suite", "-s", help=f"One of: {', '.join(SUITES)}. Default full.", rich_help_panel=RUN, show_default=False
        ),
    ] = "full",
    intensity: Annotated[
        str, typer.Option("--intensity", "-i", help=f"One of: {', '.join(INTENSITIES)}.", rich_help_panel=RUN)
    ] = "standard",
    skills: Annotated[
        list[str] | None,
        typer.Option(
            "--skills", help="Use only these skills (repeatable; see `agentlab skills list`).", rich_help_panel=RUN
        ),
    ] = None,
    exclude_skills: Annotated[
        list[str] | None, typer.Option("--exclude-skills", help="Never use these skills.", rich_help_panel=RUN)
    ] = None,
    tests: Annotated[
        list[Path] | None,
        typer.Option("--tests", help="YAML/JSON file with your own test cases (repeatable).", rich_help_panel=RUN),
    ] = None,
    profile: Annotated[
        str | None, typer.Option("--profile", help="Scoring profile name or file.", rich_help_panel=RUN)
    ] = None,
    requirement: Annotated[
        list[str] | None,
        typer.Option(
            "--requirement",
            help="A business rule the agent must follow (repeatable). A model designs a test for it, which needs "
            "evaluation.llm_test_generation; a rule that gets no test is named in the plan.",
            rich_help_panel=RUN,
        ),
    ] = None,
    no_second_wave: Annotated[
        bool,
        typer.Option("--no-second-wave", help="Skip the adaptive second wave of follow-up tests.", rich_help_panel=RUN),
    ] = False,
    no_judge: Annotated[
        bool, typer.Option("--no-judge", help="Deterministic checks only; no LLM judge.", rich_help_panel=RUN)
    ] = False,
    no_probe: Annotated[
        bool,
        typer.Option(
            "--no-probe",
            help="Do not send discovery probes (questions) to the target. Its reachability is still checked.",
            rich_help_panel=RUN,
        ),
    ] = False,
    baseline: Annotated[
        str | None,
        typer.Option("--baseline", help="Regression: re-run the tests of this earlier RUN_ID.", rich_help_panel=RUN),
    ] = None,
    only: Annotated[
        list[str] | None,
        typer.Option(
            "--only",
            help="Run just this test id (repeatable); with --baseline RUN_ID it replays the exact test of that run.",
            rich_help_panel=RUN,
        ),
    ] = None,
    project: Annotated[
        str, typer.Option("--project", help="Project the run belongs to.", rich_help_panel=RUN)
    ] = "default",
    max_tests: Annotated[
        int | None, typer.Option("--max-tests", help="Upper bound on the number of tests.", rich_help_panel=LIMITS)
    ] = None,
    max_cost: Annotated[
        float | None, typer.Option("--max-cost", help="Stop at this cost in USD.", rich_help_panel=LIMITS)
    ] = None,
    max_time: Annotated[
        float | None, typer.Option("--max-time", help="Stop after this many seconds.", rich_help_panel=LIMITS)
    ] = None,
    parallel: Annotated[
        int | None, typer.Option("--parallel", help="Tests run at the same time.", rich_help_panel=LIMITS)
    ] = None,
    repetitions: Annotated[
        int | None,
        typer.Option("--repetitions", help="Repeat every test this many times.", rich_help_panel=LIMITS),
    ] = None,
    plan_only: Annotated[
        bool,
        typer.Option(
            "--plan-only", help="Design and show the plan; send nothing but harmless probes.", rich_help_panel=OUT
        ),
    ] = False,
    plan_detail: Annotated[
        bool, typer.Option("--plan-detail", help="Show every test of the plan.", rich_help_panel=OUT)
    ] = False,
    plan_output: Annotated[
        Path | None,
        typer.Option("--plan-output", help="Write the plan to this file (.md or .json).", rich_help_panel=OUT),
    ] = None,
    confirm: Annotated[
        bool, typer.Option("--confirm", help="Show the plan and ask before running it.", rich_help_panel=OUT)
    ] = False,
    fail_on: Annotated[
        str,
        typer.Option(
            "--fail-on",
            help=f"Exit with code 1 when a finding reaches this severity ({', '.join(FAIL_ON)}).",
            rich_help_panel=OUT,
        ),
    ] = "high",
    report: Annotated[
        list[str] | None,
        typer.Option(
            "--report",
            help="Report formats written at the end of the run: json, md, html, pdf, all or none "
            "(repeat or separate with commas). Default: reporting.formats of the configuration.",
            rich_help_panel=OUT,
        ),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print the result as JSON.", rich_help_panel=OUT)] = False,
    quiet: Annotated[
        bool, typer.Option("--quiet", "-q", help="Print problems and the summary only.", rich_help_panel=OUT)
    ] = False,
    show_tests: Annotated[
        bool, typer.Option("--show-tests", help="Print every test as it finishes.", rich_help_panel=OUT)
    ] = False,
    keep_workspace: Annotated[
        bool,
        typer.Option("--keep-workspace", help="Keep the temporary work folder for debugging.", rich_help_panel=OUT),
    ] = False,
) -> None:
    """Test an agent: discover it, design an explainable plan, run it safely, evaluate, score and report.

    Exit codes: 0 completed, no finding at --fail-on; 1 finding at --fail-on; 2 invalid input; 3 run incomplete;
    4 nothing could be tested.
    """
    if fail_on not in FAIL_ON:
        raise UserError(f"--fail-on must be one of {', '.join(FAIL_ON)}")
    st = state(ctx)
    spec = build_target(
        target_file=target,
        name=name,
        repo=repo,
        ref=ref,
        url=url,
        api_url=api_url,
        openapi=openapi,
        mcp_url=mcp_url,
        mcp_command=mcp_command,
        command=command,
        llm=llm,
        system_prompt=system_prompt,
        docs=docs,
        description=description,
        objective=objective,
        credentials=credentials,
        mock=mock,
        authorize=authorize,
        authorization_note=authorization_note,
        disposable=disposable_environment,
        production=production,
    )
    _require_something(spec)
    services = make_services(
        st,
        overrides={
            "limits.max_cost_usd": max_cost,
            "limits.max_execution_time_seconds": max_time,
            "max_parallel": parallel,
            "evaluation.repetitions": repetitions,
            "reporting.formats": report_formats(report),
        },
    )
    if spec.llm is not None:
        services.config.provider(spec.llm.provider)  # fail early (exit 2) when the provider is not configured
    if baseline:
        baseline = resolve_run_id(services, baseline)  # an id prefix is enough
    options = RunOptions(
        suite=suite,
        intensity=intensity,
        include_skills=skills,
        exclude_skills=exclude_skills,
        user_test_files=list(tests or []),
        scoring_profile=profile,
        second_wave=not no_second_wave,
        max_tests=max_tests,
        judge=not no_judge,
        objective=objective,
        requirements=list(requirement or []),
        probe=not no_probe,
        project=project,
        plan_only=plan_only,
        run_id=new_id(),
        baseline_run_id=baseline,
        only_tests=list(only or []),
        keep_workspace=keep_workspace,
    )
    flags = _Flags(
        confirm=confirm,
        fail_on=fail_on,
        as_json=as_json,
        quiet=quiet,
        show_tests=show_tests,
        plan_detail=plan_detail,
        plan_output=plan_output,
    )
    try:
        code = run_async(_run(services, spec, options, flags))
    except asyncio.CancelledError:
        err.print("[yellow]aborted; the run is recorded as cancelled[/yellow]")
        code = EXIT_INCOMPLETE
    raise typer.Exit(code)


class _Flags:
    def __init__(self, **kw: Any) -> None:
        self.confirm: bool = kw["confirm"]
        self.fail_on: str = kw["fail_on"]
        self.as_json: bool = kw["as_json"]
        self.quiet: bool = kw["quiet"]
        self.show_tests: bool = kw["show_tests"]
        self.plan_detail: bool = kw["plan_detail"]
        self.plan_output: Path | None = kw["plan_output"]


async def _run(services: Services, spec: TargetSpec, options: RunOptions, flags: _Flags) -> int:
    bus = EventBus()
    orch = TestOrchestratorAgent(services, bus=bus)
    assert options.run_id is not None
    run_id = options.run_id
    token = orch.token_for(run_id)
    out = err if flags.as_json else console  # stdout carries only the JSON document in --json mode
    bus.subscribe(ProgressPrinter(out, run_id, quiet=flags.quiet or flags.as_json, show_tests=flags.show_tests))
    main = asyncio.current_task()
    presses = 0

    def on_interrupt() -> None:
        nonlocal presses
        presses += 1
        if presses == 1:
            token.cancel("interrupted from the terminal (Ctrl-C)")
            err.print(
                "[yellow]stopping: the running step finishes, the rest is skipped and what ran is reported "
                "(press Ctrl-C again to abort now)[/yellow]"
            )
        elif main is not None:
            main.cancel()

    loop = asyncio.get_running_loop()
    handled = False
    with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):  # not the main thread / not POSIX
        loop.add_signal_handler(signal.SIGINT, on_interrupt)
        handled = True
    try:
        prepared = await orch.prepare(spec, options)
        try:
            return await _after_prepare(orch, prepared, options, flags)
        finally:
            await prepared.aclose()
    finally:
        if handled:
            loop.remove_signal_handler(signal.SIGINT)
        await services.aclose()


async def _after_prepare(orch: TestOrchestratorAgent, prepared: PreparedRun, options: RunOptions, flags: _Flags) -> int:
    plan = prepared.plan
    if flags.plan_output:
        _write_plan(plan, flags.plan_output)
    if (options.plan_only or flags.confirm) and not flags.as_json:
        render_profile(console, prepared.profile, prepared.warnings)
        render_plan(console, plan, detail=flags.plan_detail)
    if options.plan_only:
        orch.finish_plan_only(prepared)
        if flags.as_json:
            emit_json({"run_id": prepared.run_id, "plan": plan.model_dump(mode="json"), "warnings": prepared.warnings})
        else:
            console.print(f"[dim]plan stored with run {prepared.run_id}; nothing was run against the target[/dim]")
        return EXIT_OK if plan.counts()["runnable"] else EXIT_NOT_TESTED
    if flags.confirm:
        if not sys.stdin.isatty():
            raise UserError("--confirm needs an interactive terminal; use --plan-only to review a plan, then run it")
        if not typer.confirm("Run this plan against the target?", default=False):
            orch.finish_plan_only(prepared)
            console.print(f"[yellow]not run[/yellow] (the plan is stored with run {prepared.run_id})")
            return EXIT_OK
    outcome = await orch.execute(prepared, cancel=orch.token_for(prepared.run_id))
    if flags.as_json:
        emit_json(outcome.to_dict())
    else:
        render_outcome(console, outcome)
    return exit_code(outcome, flags.fail_on)


def _write_plan(plan: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".json":
        path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    else:
        path.write_text(plan_markdown(plan, detail=True), encoding="utf-8")
