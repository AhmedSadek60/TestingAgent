"""The fixture self-test: proof that AgentLab finds what was planted (spec sections 23 and 64).

Every fixture kind ships three things next to its code (``fixtures/data``):

* ``<kind>.expected.yaml``: for each planted defect, which tests must catch it and how serious the finding must be;
* ``<kind>.dataset.yaml``: business scenarios (the kind of tests an owner writes) that the correct agent passes;
* the fixture itself, in a *correct* build (no defects) and a *flawed* build (any subset of defects).

:func:`verify_kind` then asserts, against real HTTP services and the real orchestrator:

1. the **correct** build passes the whole suite: no failed test, no finding, nothing BLOCKED that is not accounted for.
   A suite that cries wolf is as useless as one that misses defects.
2. each **defect alone** is caught: at least one of the tests named for it fails, and the finding it produces is at least
   as serious as expected. Defects are checked one at a time so that one noisy defect cannot hide a missed one.
3. **all defects together** are caught by the full suite and the scorecard does not call the agent good.

The self-test runs without any LLM judge (it must be repeatable offline), so tests that need one are BLOCKED, never
passed; the expectation files list the defects that only a judge could separate from a pass.

Kinds whose agent executes code (a coding agent) or needs a browser declare ``requires``. When the machine lacks it, the
kind is *skipped* and the report says why; the agent is never run unprotected to get around the missing sandbox.
"""

from __future__ import annotations

import asyncio
import fnmatch
import multiprocessing
import tempfile
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import Field

from agentlab.browser.environment import browser_status
from agentlab.core.config import AgentLabConfig, ReportingConfig, SandboxConfig, SecurityConfig, StorageConfig
from agentlab.core.enums import Severity, TestStatus
from agentlab.core.models import TargetSpec
from agentlab.core.models.base import Model
from agentlab.fixtures import REGISTRY, fixture_class
from agentlab.orchestrator import RunOptions, TestOrchestratorAgent
from agentlab.sandbox.docker import DockerSandboxProvider
from agentlab.security import safeyaml
from agentlab.services import Services

DATA_DIR = Path(__file__).parent / "data"
JUDGE_REASON = "judge"  # a test BLOCKED because no LLM judge is configured says so in its reason


class DefectExpectation(Model):
    """What AgentLab must report for one planted defect."""

    detected_by: list[str] = Field(description="shell-style patterns of test ids; at least one such test must FAIL")
    min_severity: Severity = Severity.LOW
    security: bool = False  # the finding must be flagged as a security finding
    intensity: str | None = None  # needs a deeper suite than the kind's default
    config: dict[str, Any] = Field(default_factory=dict)  # configuration the owner would set to see this defect
    why: str = ""  # how the defect shows, for the reader of this file


class KindExpectation(Model):
    kind: str
    summary: str = ""
    intensity: str = "standard"
    dataset: str | None = None  # file name inside fixtures/data
    config: dict[str, Any] = Field(default_factory=dict)
    allowed_blocked: dict[str, str] = Field(
        default_factory=dict, description="patterns of test ids that stay BLOCKED on the correct build, with the reason"
    )
    defects: dict[str, DefectExpectation]
    flawed_grade_at_most: str = "C"  # the all-defects scorecard must not rate the agent better than this


def expectation_path(kind: str) -> Path:
    return DATA_DIR / f"{kind}.expected.yaml"


def load_expectation(kind: str) -> KindExpectation:
    path = expectation_path(kind)
    if not path.is_file():
        raise FileNotFoundError(f"fixture '{kind}' has no expected-findings file ({path.name})")
    return KindExpectation.model_validate(safeyaml.load(path.read_text(encoding="utf-8")))


def dataset_path(kind: str) -> Path | None:
    name = load_expectation(kind).dataset
    return DATA_DIR / name if name else None


def audit_expectation(kind: str) -> list[str]:
    """Problems with the expected-findings file itself (a defect without an expectation, or one for nothing)."""
    cls, exp = fixture_class(kind), load_expectation(kind)
    problems = [f"defect '{d}' has no expectation" for d in cls.DEFECTS if d not in exp.defects]
    problems += [f"expectation for '{d}', which the fixture does not have" for d in exp.defects if d not in cls.DEFECTS]
    if exp.dataset and not (DATA_DIR / exp.dataset).is_file():
        problems.append(f"dataset {exp.dataset} does not exist")
    return problems


# ------------------------------------------------------------------------------------------------------ one run
@dataclass
class RunSummary:
    """The parts of one orchestrated run that the self-test looks at."""

    variant: str
    tests: int = 0
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    blocked: dict[str, str] = field(default_factory=dict)
    findings: list[tuple[str, Severity, bool]] = field(default_factory=list)  # (test id, severity, is_security)
    overall: float | None = None
    grade: str = ""
    security_posture: str = ""
    error: str | None = None

    @property
    def failing(self) -> list[str]:
        return [*self.failed, *self.errors]

    def worst(self, tests: Iterable[str]) -> Severity | None:
        wanted = set(tests)
        found = [s for t, s, _ in self.findings if t in wanted]
        return max(found, key=lambda s: s.rank) if found else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant": self.variant,
            "tests": self.tests,
            "passed": len(self.passed),
            "failed": self.failed,
            "errors": self.errors,
            "blocked": self.blocked,
            "findings": [{"test": t, "severity": s.value, "security": sec} for t, s, sec in self.findings],
            "overall": self.overall,
            "grade": self.grade,
            "security_posture": self.security_posture,
            "error": self.error,
        }


def _merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in extra.items():
        out[key] = _merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def _config(tmp: Path, overrides: dict[str, Any]) -> AgentLabConfig:
    base = AgentLabConfig(
        storage=StorageConfig(
            database_url=f"sqlite:///{tmp}/lab.db",
            artifacts_dir=str(tmp / "artifacts"),
            secrets_file=str(tmp / "s.enc"),
        ),
        security=SecurityConfig(sandbox=SandboxConfig(provider="disabled")),
        reporting=ReportingConfig(formats=[]),  # the self-test checks what is found, not how it is written up
    )
    return AgentLabConfig.model_validate(_merge(base.model_dump(mode="json"), overrides)) if overrides else base


def run_fixture(
    kind: str,
    variant: str,
    *,
    intensity: str = "standard",
    only: list[str] | None = None,
    dataset: Path | None = None,
    config: dict[str, Any] | None = None,
    token: str | None = None,
) -> RunSummary:
    """Serve one build of a fixture on a free loopback port and run AgentLab against it (blocking)."""
    agent = fixture_class(kind).build(variant)
    summary = RunSummary(variant=variant)
    with tempfile.TemporaryDirectory(prefix="agentlab-selftest-") as raw, agent.deployed(token=token) as target:
        tmp = Path(raw)
        services = Services.create(_config(tmp, config or {}), base_dir=tmp)
        try:
            spec = TargetSpec(**target)
            options = RunOptions(
                intensity=intensity,
                second_wave=False,
                only_tests=list(only or []),
                user_test_files=[dataset] if dataset else [],
            )
            out = asyncio.run(TestOrchestratorAgent(services).run(spec, options))
        finally:
            services.store.db.dispose()
    summary.tests = len(out.results)
    for r in out.results:
        if r.status == TestStatus.PASSED:
            summary.passed.append(r.test_id)
        elif r.status == TestStatus.FAILED or r.status == TestStatus.TIMEOUT:
            summary.failed.append(r.test_id)
        elif r.status == TestStatus.ERROR:
            summary.errors.append(r.test_id)
        elif r.status == TestStatus.BLOCKED:
            summary.blocked[r.test_id] = r.blocked_reason or ""
    summary.findings = [(f.test_id, f.severity, f.is_security) for f in out.findings]
    if out.scorecard is not None:
        summary.overall = out.scorecard.overall
        summary.grade = out.scorecard.grade or ""
    summary.security_posture = str(out.security.posture) if out.security is not None else ""
    summary.error = out.error
    return summary


# ------------------------------------------------------------------------------------------------ the three checks
@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    run: RunSummary | None = None


@dataclass
class KindReport:
    kind: str
    checks: list[Check] = field(default_factory=list)
    skipped: str = ""  # why this kind could not be verified on this machine (it was not run, and nothing was faked)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "ok": self.ok,
            "skipped": self.skipped or None,
            "checks": [
                {"name": c.name, "ok": c.ok, "detail": c.detail, "run": c.run.to_dict() if c.run else None}
                for c in self.checks
            ],
        }


def prerequisite_problems(kind: str) -> list[str]:
    """What this machine lacks to verify ``kind`` (empty when it can). A fixture that executes code needs the sandbox it is
    meant to be tested in; without one the kind is skipped and reported as such, never run on the host."""
    problems: list[str] = []
    for need in fixture_class(kind).requires:
        if need == "docker":
            ok, why = asyncio.run(DockerSandboxProvider().available())
            if not ok:
                problems.append(f"Docker is required: {why}")
        elif need == "browser":
            ok, why = browser_status(AgentLabConfig())
            if not ok:
                problems.append(f"a browser is required: {why}")
        else:
            problems.append(f"unknown prerequisite '{need}'")
    return problems


def _match(patterns: Iterable[str], names: Iterable[str]) -> list[str]:
    pats = list(patterns)
    return [n for n in names if any(fnmatch.fnmatchcase(n, p) for p in pats)]


def check_correct(exp: KindExpectation, run: RunSummary) -> Check:
    problems: list[str] = []
    if run.error:
        problems.append(f"the run itself failed: {run.error}")
    if run.failing:
        problems.append(f"tests failed on the correct agent: {', '.join(run.failing)}")
    if run.findings:
        problems.append(f"findings on the correct agent: {', '.join(sorted({t for t, _, _ in run.findings}))}")
    stray = [
        t for t, why in run.blocked.items() if JUDGE_REASON not in why.lower() and not _match(exp.allowed_blocked, [t])
    ]
    if stray:
        problems.append(f"tests BLOCKED without a reason in the expectation file: {', '.join(stray)}")
    if not run.passed:
        problems.append("no test passed")
    return Check("correct build is clean", not problems, "; ".join(problems), run)


def check_defect(name: str, exp: DefectExpectation, run: RunSummary) -> Check:
    label = f"defect {name} is detected"
    if run.error:
        return Check(label, False, f"the run itself failed: {run.error}", run)
    caught = _match(exp.detected_by, run.failing)
    if not caught:
        ran = _match(exp.detected_by, [*run.passed, *run.blocked])
        why = f"expected one of {exp.detected_by} to fail; "
        if ran:
            why += "they ran and passed or were blocked: " + ", ".join(ran)
        else:
            why += "none of them ran"
        return Check(label, False, why, run)
    worst = run.worst(caught)
    if worst is None:
        return Check(label, False, f"{', '.join(caught)} failed but produced no finding", run)
    if worst.rank < exp.min_severity.rank:
        return Check(label, False, f"worst finding is {worst.value}, expected at least {exp.min_severity.value}", run)
    if exp.security and not any(sec for t, _, sec in run.findings if t in caught):
        return Check(label, False, "the finding is not flagged as a security finding", run)
    return Check(label, True, f"{', '.join(caught)} -> {worst.value}", run)


def check_flawed(exp: KindExpectation, flawed: RunSummary, correct: RunSummary) -> Check:
    problems: list[str] = []
    if flawed.error:
        problems.append(f"the run itself failed: {flawed.error}")
    if not flawed.failing:
        problems.append("no test failed")
    if flawed.overall is not None and correct.overall is not None and flawed.overall >= correct.overall:
        problems.append(f"score {flawed.overall} is not below the correct agent's {correct.overall}")
    if flawed.grade and flawed.grade[:1] < exp.flawed_grade_at_most:  # "A" < "B" < "C" < "D" < "F"
        problems.append(
            f"the scorecard still rates the flawed agent '{flawed.grade}', better than {exp.flawed_grade_at_most}"
        )
    return Check("all defects together are detected", not problems, "; ".join(problems), flawed)


# --------------------------------------------------------------------------------------------------- the driver
Job = tuple[
    str, str, list[str] | None, str, str | None, dict[str, Any]
]  # kind, variant, only, intensity, dataset, config


def _job(job: Job) -> RunSummary:
    kind, variant, only, intensity, dataset, config = job
    return run_fixture(
        kind, variant, intensity=intensity, only=only, dataset=Path(dataset) if dataset else None, config=config
    )


class _Inline:
    """The executor for ``workers=1``: runs each job in the calling process (debuggable, no start-up cost)."""

    def submit(self, fn: Callable[[Job], RunSummary], job: Job) -> Future[RunSummary]:
        future: Future[RunSummary] = Future()
        try:
            future.set_result(fn(job))
        except BaseException as exc:  # a job that raises is reported by the caller, not lost
            future.set_exception(exc)
        return future

    def __enter__(self) -> _Inline:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def _executor(workers: int) -> Any:
    """Runs are CPU-bound Python (planning, evaluation, a service per run), so parallelism needs processes, not
    threads. ``spawn`` because a forked copy of a process that is serving HTTP on threads is not safe."""
    if workers <= 1:
        return _Inline()
    return ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"))


def jobs_for(kind: str, *, defects: list[str] | None = None, all_defects: bool = True) -> dict[str, Job]:
    """Every run needed to verify ``kind``: the correct build, each defect alone, and all defects together."""
    exp, cls = load_expectation(kind), fixture_class(kind)
    dataset = dataset_path(kind)
    ds = str(dataset) if dataset else None
    jobs: dict[str, Job] = {"correct": (kind, "correct", None, exp.intensity, ds, exp.config)}
    for name in defects or list(cls.DEFECTS):
        d = exp.defects[name]
        jobs[name] = (kind, name, list(d.detected_by), d.intensity or exp.intensity, ds, _merge(exp.config, d.config))
    if all_defects:
        jobs["flawed"] = (kind, "flawed", None, exp.intensity, ds, exp.config)
    return jobs


def assemble(kind: str, done: dict[str, RunSummary], *, defects: list[str] | None = None) -> KindReport:
    exp, cls = load_expectation(kind), fixture_class(kind)
    report = KindReport(kind)
    correct = done["correct"]
    report.checks.append(check_correct(exp, correct))
    for name in defects or list(cls.DEFECTS):
        report.checks.append(check_defect(name, exp.defects[name], done[name]))
    if "flawed" in done:
        report.checks.append(check_flawed(exp, done["flawed"], correct))
    return report


def verify(
    kinds: list[str] | None = None,
    *,
    workers: int = 4,
    defects: list[str] | None = None,
    all_defects: bool = True,
    progress: Callable[[str], None] | None = None,
) -> list[KindReport]:
    """Run the three checks for the given fixture kinds (default: all). All runs of all kinds share one pool."""
    chosen = kinds or sorted(REGISTRY)
    say = progress or (lambda _msg: None)
    reports: dict[str, KindReport] = {}
    plan: dict[str, dict[str, Job]] = {}
    for kind in chosen:
        problems = audit_expectation(kind)
        missing = [] if problems else prerequisite_problems(kind)
        if problems:
            reports[kind] = KindReport(kind, [Check("expected-findings file is complete", False, "; ".join(problems))])
        elif missing:
            reports[kind] = KindReport(kind, skipped="; ".join(missing))
            say(f"{kind}: skipped ({reports[kind].skipped})")
        else:
            plan[kind] = jobs_for(kind, defects=defects, all_defects=all_defects)
    with _executor(workers) as pool:
        futures = {(kind, name): pool.submit(_job, job) for kind, jobs in plan.items() for name, job in jobs.items()}
        done: dict[str, dict[str, RunSummary]] = {kind: {} for kind in plan}
        for (kind, name), future in futures.items():
            try:
                done[kind][name] = future.result()
            except Exception as exc:  # the harness itself failing is a failed check, not a crash of the others
                done[kind][name] = RunSummary(variant=name, error=f"{type(exc).__name__}: {exc}")
            say(f"{kind}: {name}")
    for kind in plan:
        reports[kind] = assemble(kind, done[kind], defects=defects)
    return [reports[k] for k in chosen]


def verify_kind(kind: str, *, workers: int = 4, **kwargs: Any) -> KindReport:
    return verify([kind], workers=workers, **kwargs)[0]
