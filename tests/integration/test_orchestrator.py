"""End-to-end runs of the seventeen-phase orchestrator against the deterministic MockAgent (no network, no Docker)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentlab.core.config import (
    AgentLabConfig,
    EvaluationConfig,
    JudgeConfig,
    LimitsConfig,
    SandboxConfig,
    SecurityConfig,
    StorageConfig,
)
from agentlab.core.enums import EventType, Phase, RunStatus, TestStatus
from agentlab.core.errors import PolicyBlocked, UserError
from agentlab.core.models import ApiConfig, LlmTargetConfig, MockAgentConfig, TargetSpec
from agentlab.orchestrator import RunOptions, TestOrchestratorAgent
from agentlab.orchestrator.analysis import is_diagnostic
from agentlab.services import Services

TOOLS = ["send_email", "get_weather", "calculator", "delete_file"]
KNOWLEDGE = {"leave.md": "Employees receive 25 days of paid annual leave."}


def make_config(tmp_path: Path, **overrides: object) -> AgentLabConfig:
    base: dict = {
        "storage": StorageConfig(
            database_url=f"sqlite:///{tmp_path}/lab.db",
            artifacts_dir=str(tmp_path / "artifacts"),
            secrets_file=str(tmp_path / "secrets.enc"),
        ),
        "security": SecurityConfig(sandbox=SandboxConfig(provider="disabled")),  # Docker is never assumed
    }
    base.update(overrides)
    return AgentLabConfig(**base)


@pytest.fixture
def services(tmp_path: Path):
    sv = Services.create(make_config(tmp_path), base_dir=tmp_path)
    yield sv
    sv.store.db.dispose()


def mock_spec(*behaviors: str, **kw: object) -> TargetSpec:
    return TargetSpec(
        name="demo",
        description="HR assistant that answers from policy documents and can send email",
        mock=MockAgentConfig(behaviors=list(behaviors) or ["success"], tools=TOOLS, knowledge=KNOWLEDGE),
        **kw,
    )


async def test_full_run_goes_through_all_seventeen_phases_and_persists_everything(services: Services) -> None:
    orch = TestOrchestratorAgent(services)
    seen: list[tuple[str, str]] = []
    orch.bus.subscribe(
        lambda e: seen.append((e.type.value, e.payload.get("phase", ""))) if "Phase" in e.type.value else None
    )
    out = await orch.run(mock_spec(), RunOptions(intensity="quick"))

    assert out.status == RunStatus.COMPLETED
    assert [r.phase for r in out.phases] == list(Phase), "phases must be reported in the spec's order"
    started = [p for t, p in seen if t == EventType.PHASE_STARTED.value]
    completed = [p for t, p in seen if t == EventType.PHASE_COMPLETED.value]
    assert started[0] == "input_validation" and sorted(started) == sorted(p.value for p in Phase)
    assert sorted(completed) == sorted(p.value for p in Phase)
    streamed = {r.phase for r in out.phases if r.streamed}
    assert streamed == {Phase.TRACE_COLLECTION, Phase.DETERMINISTIC_EVALUATION, Phase.LLM_JUDGE_EVALUATION}

    store = services.store
    run = store.get_run(out.run_id)
    assert run["status"] == "completed" and run["suite_id"] and run["manifest"]["plan"]["hash"]
    assert len(store.list_results(out.run_id)) == len(out.results) > 50
    assert store.latest_scorecard(out.run_id) is not None
    assert {e["type"] for e in store.list_events(out.run_id, limit=100000)} >= {
        "RunStarted",
        "PhaseStarted",
        "SkillSelected",
        "TestPlanGenerated",
        "TestStarted",
        "TestCompleted",
        "RunCompleted",
    }
    assert store.list_traces(out.run_id)
    assert {a["kind"] for a in store.list_artifacts(out.run_id)} >= {"plan", "trace", "analysis", "manifest"}
    assert store.latest_profile(run["target_id"]) is not None

    # the plan's BLOCKED prediction and the executor agree: both use the same capability rules
    pred = out.manifest["outcome"]["plan_prediction"]
    assert pred["compared"] > 50 and pred["agree"] == pred["compared"]
    # no judge configured: judged tests are BLOCKED, never failed, and the run says so
    assert out.environment.judge is False and any("judge" in q for q in out.scorecard.qualifiers)
    assert out.phases[10].status == "skipped"
    # security posture is never 'secure' when categories could not be tested
    assert out.security.posture in {"partially_tested", "no_vulnerabilities_observed"}
    assert out.security.caveats


async def test_flawed_agent_gets_a_capped_and_qualified_rating(services: Services) -> None:
    orch = TestOrchestratorAgent(services)
    out = await orch.run(mock_spec("unsafe_behavior"), RunOptions(intensity="quick"))

    assert out.security.posture == "vulnerabilities_observed"
    assert out.security.leaks, "the canary the agent disclosed must be reported as a leak"
    leak = out.security.leaks[0]
    assert leak.channels and leak.severity in {"critical", "high"}
    sc = out.scorecard
    assert sc.security_cap_applied and sc.overall <= 65 and "capped by security" in (sc.grade or "")
    assert any("unqualified" in q for q in sc.qualifiers)
    assert [f for f in out.findings if f.is_security and f.severity.value == "critical"]
    # a critical finding was raised live, as an event, while the run was still going
    kinds = {e["type"] for e in services.store.list_events(out.run_id, limit=100000)}
    assert {"FindingCreated", "SecurityAlert"} <= kinds


async def test_wave_two_digs_into_failures_and_is_not_scored_twice(services: Services) -> None:
    orch = TestOrchestratorAgent(services)
    out = await orch.run(mock_spec("unsafe_behavior"), RunOptions(intensity="quick"))
    assert len(out.plans) == 2 and out.plans[1].wave == 2
    child_ids = {p.id for p in out.plans[1].tests}
    variants = [t for t in out.tests if t.id in child_ids and is_diagnostic(t)]
    assert variants, "failed tests must be re-tried in rephrased form"
    assert all(t.context.get("variant_of") or t.context.get("recheck_of") for t in variants)
    # diagnostics are evidence: they are executed, stored and folded into the finding, but not scored again
    assert any("Wave-2 follow-up" in line for f in out.findings for line in f.inferences), (
        "the original finding must say whether the weakness survives rephrasing"
    )
    assert any("not scored" in n for n in out.scorecard.notes)
    assert out.cross_test.adaptive


async def test_clean_first_wave_adds_no_second_wave(services: Services) -> None:
    orch = TestOrchestratorAgent(services)
    spec = TargetSpec(name="plain", description="a bare chatbot", mock=MockAgentConfig(behaviors=["success"]))
    out = await orch.run(spec, RunOptions(suite="discovery", intensity="quick", second_wave=True))
    failed = [r for r in out.results if r.status == TestStatus.FAILED]
    if not failed:
        assert len(out.plans) == 1
    assert out.status == RunStatus.COMPLETED


async def test_plan_only_stops_before_anything_is_sent_to_the_target(services: Services) -> None:
    orch = TestOrchestratorAgent(services)
    out = await orch.run(mock_spec(), RunOptions(intensity="quick", plan_only=True))
    assert out.status == RunStatus.COMPLETED and out.results == [] and out.scorecard is None
    assert out.plans[0].tests and out.plans[0].coverage and out.plans[0].security_coverage
    events = {e["type"] for e in services.store.list_events(out.run_id, limit=100000)}
    assert "TestStarted" not in events and "TestPlanGenerated" in events
    assert services.store.get_run(out.run_id)["totals"]["plan_only"] is True


async def test_cancelling_skips_the_remaining_tests_but_still_scores_what_ran(services: Services) -> None:
    orch = TestOrchestratorAgent(services)
    done = {"n": 0}

    def stop_after_ten(e) -> None:  # type: ignore[no-untyped-def]
        if e.type == EventType.TEST_COMPLETED:
            done["n"] += 1
            if done["n"] == 10:
                orch.cancel(e.run_id, "stopped by the test")

    orch.bus.subscribe(stop_after_ten)
    out = await orch.run(mock_spec(), RunOptions(intensity="quick"))
    assert out.status == RunStatus.CANCELLED
    counts = out.counts
    assert counts.get("skipped", 0) > 0 and counts.get("passed", 0) + counts.get("failed", 0) >= 1
    assert len(out.plans) == 1, "no adaptive wave after a cancellation"
    assert any("cancelled" in q for q in out.scorecard.qualifiers)
    assert services.store.get_run(out.run_id)["status"] == "cancelled"


async def test_a_run_level_budget_stops_the_run_instead_of_failing_tests(tmp_path: Path) -> None:
    cfg = make_config(tmp_path, limits=LimitsConfig(max_tokens=40))
    sv = Services.create(cfg, base_dir=tmp_path)
    out = await TestOrchestratorAgent(sv).run(mock_spec(), RunOptions(intensity="quick"))
    stopped = [r for r in out.results if r.status.is_stopped]
    assert stopped and out.status.value.startswith("stopped_due_to")
    assert not any(r.status == TestStatus.FAILED and "budget" in (r.blocked_reason or "") for r in out.results)
    assert any("stopped early" in q for q in out.scorecard.qualifiers)
    sv.store.db.dispose()


async def test_an_unreachable_api_is_reported_not_failed_and_nothing_is_scored(services: Services) -> None:
    spec = TargetSpec(name="down", description="an api that is down", api=ApiConfig(url="http://127.0.0.1:9/chat"))
    out = await TestOrchestratorAgent(services).run(spec, RunOptions(suite="discovery", intensity="quick"))
    assert "api" in out.environment.unreachable
    assert out.executed == 0 and not any(r.status == TestStatus.FAILED for r in out.results)
    assert any(w.code == "interface_unavailable" and w.level == "blocker" for w in out.plans[0].warnings)
    assert any("unavailable" in w for w in out.warnings)
    assert out.scorecard.overall is None, "nothing ran, so there is no score to give"
    assert out.summary()["tests"] == 0 and out.tested is False


async def test_a_judge_that_is_the_target_itself_is_never_used(tmp_path: Path) -> None:
    cfg = make_config(tmp_path, evaluation=EvaluationConfig(judges=[JudgeConfig(provider="mock", model="mock-judge")]))
    sv = Services.create(cfg, base_dir=tmp_path)
    spec = TargetSpec(
        name="self-judge",
        llm=LlmTargetConfig(provider="mock", model="mock-judge", system_prompt="You are helpful."),
    )
    orch = TestOrchestratorAgent(sv)
    prepared = await orch.prepare(spec, RunOptions(intensity="quick", plan_only=True))
    try:
        env = prepared.environment
        assert env.judge is False and env.judge_independent is False
        assert "target" in env.judge_note and prepared.judge is None
        assert prepared.manifest["judges"]["enabled"] is False
    finally:
        await prepared.aclose()
    sv.store.db.dispose()


async def test_a_different_judge_is_accepted_and_recorded(tmp_path: Path) -> None:
    from agentlab.core.config import ProviderConfig

    cfg = make_config(
        tmp_path,
        providers=[
            ProviderConfig(name="mock", type="mock", model="mock-target"),
            ProviderConfig(name="judge", type="mock", model="mock-judge"),
        ],
        evaluation=EvaluationConfig(judges=[JudgeConfig(provider="judge", model="mock-judge")]),
    )
    sv = Services.create(cfg, base_dir=tmp_path)
    spec = TargetSpec(name="t", llm=LlmTargetConfig(provider="mock", model="mock-target"))
    orch = TestOrchestratorAgent(sv)
    prepared = await orch.prepare(spec, RunOptions(intensity="quick", plan_only=True))
    try:
        assert prepared.environment.judge is True and prepared.judge is not None
        assert prepared.manifest["judges"]["judges"][0]["provider"] == "judge"
    finally:
        await prepared.aclose()
    sv.store.db.dispose()


async def test_validation_rejects_unusable_requests_before_any_work(services: Services) -> None:
    orch = TestOrchestratorAgent(services)
    with pytest.raises(UserError, match="nothing to test"):
        await orch.prepare(TargetSpec(name="empty"))
    with pytest.raises(UserError, match="unknown suite"):
        await orch.prepare(mock_spec(), RunOptions(suite="everything"))
    with pytest.raises(UserError, match="unknown skill"):
        await orch.prepare(mock_spec(), RunOptions(include_skills=["no-such-skill"]))
    with pytest.raises(UserError, match="intensity"):
        await orch.prepare(mock_spec(), RunOptions(intensity="extreme"))
    meta = TargetSpec(name="meta", api=ApiConfig(url="http://169.254.169.254/latest/meta-data/"))
    with pytest.raises(PolicyBlocked, match="metadata"):
        await orch.prepare(meta)
    # a failed preparation is recorded, not silently dropped
    runs = services.store.list_runs()
    assert runs and all(r["status"] == "failed" for r in runs)


async def test_a_regression_run_replays_the_same_tests(services: Services) -> None:
    orch = TestOrchestratorAgent(services)
    first = await orch.run(mock_spec(), RunOptions(intensity="quick", second_wave=False))
    again = await orch.run(mock_spec(), RunOptions(baseline_run_id=first.run_id, intensity="quick", second_wave=False))
    assert again.plans[0].suite == "regression"
    assert {t.id for t in again.tests} == {t.id for t in first.tests}
    assert again.plans[0].plan_hash and again.manifest["options"]["baseline_run_id"] == first.run_id
    same = {r.test_id: r.status for r in first.results} == {r.test_id: r.status for r in again.results}
    assert same, "an unchanged deterministic target must reproduce its own results"


async def test_user_supplied_tests_join_the_plan_and_run(services: Services, tmp_path: Path) -> None:
    f = tmp_path / "mine.yaml"
    f.write_text(
        "- name: Capital question\n  input: What is the capital of France?\n  must_contain: [Paris]\n"
        "  severity_on_failure: high\n"
    )
    out = await TestOrchestratorAgent(services).run(
        mock_spec(), RunOptions(intensity="quick", user_test_files=[f], second_wave=False)
    )
    mine = [r for r in out.results if r.test_id.startswith("USER-")]
    assert len(mine) == 1 and mine[0].status == TestStatus.PASSED
    planned = next(p for p in out.plans[0].tests if p.id == mine[0].test_id)
    assert planned.origin == "user"
