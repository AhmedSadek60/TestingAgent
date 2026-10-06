"""CLI plumbing that must hold regardless of the terminal: target building, the exit-code contract, log redaction and
the database guard."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml

from agentlab.cli.cmd_test import exit_code
from agentlab.cli.targets import build_target, load_target_file
from agentlab.core.enums import RiskClass, RunStatus, Severity, TestStatus
from agentlab.core.errors import UserError
from agentlab.core.models import AgentProfile, Finding, TestResult
from agentlab.orchestrator.options import EnvironmentReport, RunOutcome
from agentlab.security.redactor import RedactingLogFilter
from agentlab.storage.db import Database


# ------------------------------------------------------------------------------------------ build_target
def test_flags_override_the_target_file_and_paths_in_the_file_are_relative_to_it(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "policy.md").write_text("policy", encoding="utf-8")
    f = tmp_path / "t.yaml"
    f.write_text(
        yaml.safe_dump({"name": "from-file", "api": {"url": "http://a/x"}, "documents": ["docs/policy.md"]}),
        encoding="utf-8",
    )
    spec = load_target_file(f)
    assert spec.documents == [str((tmp_path / "docs" / "policy.md").resolve())]
    merged = build_target(target_file=f, api_url="http://b/y", docs=["more.md"], description="d")
    assert merged.name == "from-file", "a target file keeps its own name unless --name is given"
    assert merged.api is not None and merged.api.url == "http://b/y"
    assert merged.documents[-1] == "more.md" and merged.description == "d"
    assert build_target(target_file=f, name="other").name == "other"


def test_llm_flag_accepts_model_names_with_colons_and_system_prompt_files(tmp_path: Path) -> None:
    prompt = tmp_path / "p.txt"
    prompt.write_text("You are terse.", encoding="utf-8")
    spec = build_target(llm="ollama:qwen2.5:0.5b", system_prompt=f"@{prompt}")
    assert spec.llm is not None
    assert (spec.llm.provider, spec.llm.model, spec.llm.system_prompt) == ("ollama", "qwen2.5:0.5b", "You are terse.")
    assert spec.name == "ollama-qwen2.5-0.5b"
    with pytest.raises(UserError):
        build_target(llm=":x")
    with pytest.raises(UserError):
        build_target(llm="ollama", system_prompt="@/does/not/exist")


def test_a_credential_authenticates_the_interfaces_that_do_not_name_one() -> None:
    spec = build_target(api_url="http://x/chat", credentials=["test-user"])
    assert spec.credentials == ["test-user"] and spec.api is not None and spec.api.auth_credential == "test-user"


def test_authorization_is_only_what_the_owner_states() -> None:
    plain = build_target(mock=["success"])
    assert RiskClass.HIGH_IMPACT not in plain.safety.authorized_risk_classes and not plain.safety.production
    spec = build_target(mock=["success"], authorize=["high_impact"], disposable=True, authorization_note="ok by Sam")
    assert RiskClass.HIGH_IMPACT in spec.safety.authorized_risk_classes and spec.safety.disposable_environment
    assert spec.safety.authorization_note == "ok by Sam"
    with pytest.raises(UserError):
        build_target(mock=["success"], authorize=["everything"])
    assert build_target(mock=["success"], production=True).safety.production


def test_a_missing_or_malformed_target_file_is_a_user_error(tmp_path: Path) -> None:
    with pytest.raises(UserError, match="not found"):
        load_target_file(tmp_path / "nope.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(UserError, match="mapping"):
        load_target_file(bad)
    unknown = tmp_path / "unknown.yaml"
    unknown.write_text("name: x\nnot_a_field: 1\n", encoding="utf-8")
    with pytest.raises(UserError, match="invalid target"):
        load_target_file(unknown)


# ------------------------------------------------------------------------------------------ exit codes
def outcome(status: RunStatus, results: list[TestStatus], findings: list[Severity]) -> RunOutcome:
    return RunOutcome(
        run_id="r",
        status=status,
        target="t",
        profile=AgentProfile(target_name="t"),
        plans=[],
        tests=[],
        results=[
            TestResult(run_id="r", test_id=f"T-{i}", test_name="n", category="c", score_category="c", status=s)
            for i, s in enumerate(results)
        ],
        findings=[
            Finding(
                run_id="r",
                test_id=f"T-{i}",
                title="x",
                category="c",
                severity=sev,
                confidence=0.9,
                expected="e",
                observed="o",
                impact="i",
                reproduction="r",
                recommendation="fix",
            )
            for i, sev in enumerate(findings)
        ],  # fmt: skip
        scorecard=None,
        cross_test=None,
        security=None,
        reliability=None,
        manifest={},
        environment=EnvironmentReport(),
        limits={},
        warnings=[],
        phases=[],
    )


@pytest.mark.parametrize(
    ("status", "results", "findings", "fail_on", "expected"),
    [
        (RunStatus.COMPLETED, [TestStatus.PASSED], [], "high", 0),
        (RunStatus.COMPLETED, [TestStatus.PASSED, TestStatus.FAILED], [Severity.MEDIUM], "high", 0),
        (RunStatus.COMPLETED, [TestStatus.FAILED], [Severity.HIGH], "high", 1),
        (RunStatus.COMPLETED, [TestStatus.FAILED], [Severity.CRITICAL], "high", 1),
        (RunStatus.COMPLETED, [TestStatus.FAILED], [Severity.HIGH], "critical", 0),
        (RunStatus.COMPLETED, [TestStatus.FAILED], [Severity.HIGH], "none", 0),
        (RunStatus.COMPLETED, [TestStatus.FAILED], [Severity.LOW], "low", 1),
        (RunStatus.CANCELLED, [TestStatus.PASSED], [], "high", 3),
        (RunStatus.STOPPED_DUE_TO_COST, [TestStatus.PASSED], [], "high", 3),
        (RunStatus.CANCELLED, [TestStatus.FAILED], [Severity.HIGH], "high", 1),  # evidence of a problem wins
        (RunStatus.COMPLETED, [TestStatus.BLOCKED, TestStatus.BLOCKED], [], "high", 4),  # blocked is not a pass
        (RunStatus.COMPLETED, [], [], "high", 4),
        (RunStatus.FAILED, [TestStatus.ERROR], [], "high", 4),
    ],
)
def test_exit_code_contract(status, results, findings, fail_on, expected) -> None:  # type: ignore[no-untyped-def]
    assert exit_code(outcome(status, results, findings), fail_on) == expected


# ------------------------------------------------------------------------------------------- logging
def test_log_records_are_redacted_before_any_handler_sees_them() -> None:
    records: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(self.format(record))

    handler = Capture()
    handler.addFilter(RedactingLogFilter())
    log = logging.getLogger("agentlab.test_redaction")
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    try:
        log.warning(
            "calling with Authorization: Bearer abcdefghijklmnop1234567890 for %s",
            "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUV",
        )
        try:
            raise RuntimeError("boom password=hunter2hunter2")
        except RuntimeError:
            log.exception("failed")
    finally:
        log.removeHandler(handler)
    text = "\n".join(records)
    assert "abcdefghijklmnop1234567890" not in text and "sk-ant-api03" not in text
    assert "hunter2hunter2" not in text, "tracebacks are redacted too"
    assert "calling with" in text and "failed" in text


# ---------------------------------------------------------------------------------------------- db
def test_a_database_without_migration_history_is_refused_with_a_clear_message(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path}/foreign.db"
    db = Database(url)
    db.create_all()  # tables exist but were not created by migrations
    with pytest.raises(UserError, match="no migration history"):
        db.migrate()
