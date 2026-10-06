"""The authorisation gate decides, before anything is sent, whether a test may run at all (spec sections 35 and 36).

Two kinds of "no" exist and they are never confused: a *prerequisite* is missing (BLOCKED: nothing about the agent is
known) or the owner has not *authorised* the kind of test (BLOCKED by policy). A gate that quietly let a destructive or
adversarial test through because a URL happened to be reachable would turn a test tool into an attack tool."""

from __future__ import annotations

import pytest

from agentlab.core.config import AgentLabConfig, SecurityConfig
from agentlab.core.enums import RiskClass
from agentlab.core.models import ApiConfig, ExpectedToolCall, McpConfig, SafetyPolicy, TargetSpec, TestCase, WebConfig
from agentlab.security.credentials import CredentialManager, CredentialProfile
from agentlab.security.gate import AuthorizationGate, classify_locality
from agentlab.security.redactor import SecretRedactor

LOCAL, PRIVATE, REMOTE = "http://127.0.0.1:8000/chat", "http://10.0.0.7/chat", "https://agent.example.com/chat"


def spec(url: str | None = LOCAL, **safety: object) -> TargetSpec:
    return TargetSpec(
        name="t",
        api=ApiConfig(url=url) if url else None,
        safety=SafetyPolicy(**safety),  # type: ignore[arg-type]
    )


def case(category: str = "functional", **kw: object) -> TestCase:
    return TestCase(id="T-1", name="t", category=category, objective="o", input="hi", **kw)  # type: ignore[arg-type]


def gate(target: TargetSpec, *, production_allowed: bool = False, **kw: object) -> AuthorizationGate:
    config = AgentLabConfig(security=SecurityConfig(allow_production_targets=production_allowed))
    return AuthorizationGate(target, config, **kw)  # type: ignore[arg-type]


# ======================================================================================================== locality
@pytest.mark.parametrize(
    ("url", "kind"),
    [
        ("http://localhost:3000/", "local"),
        ("http://127.0.0.1/", "local"),
        ("http://127.1.2.3/", "local"),
        ("http://[::1]/", "local"),
        ("http://app.localhost/", "local"),
        ("http://host.docker.internal:8080/", "local"),
        ("http://10.1.2.3/", "private"),
        ("http://192.168.1.20/", "private"),
        ("http://172.20.0.5/", "private"),
        ("http://intranet.local/", "private"),
        ("http://agent.corp.internal/", "private"),
        ("http://agent/", "private"),
        ("https://agent.example.com/", "remote"),
        ("http://8.8.8.8/", "remote"),
        # names and addresses that only look local
        ("http://localhost.evil.com/", "remote"),
        ("http://127.0.0.1.nip.io/", "remote"),
        ("http://localhost@evil.com/", "remote"),
        ("http://10.0.0.1.evil.com/", "remote"),
        ("http://evil.com/localhost", "remote"),
    ],
)
def test_where_a_target_is_decides_what_may_be_done_to_it(url: str, kind: str) -> None:
    assert classify_locality(spec(url)) == kind


def test_a_target_with_several_addresses_is_as_remote_as_its_most_remote_one() -> None:
    mixed = TargetSpec(
        name="t",
        api=ApiConfig(url=LOCAL),
        web=WebConfig(url="https://shop.example.com/"),
        mcp=McpConfig(url="http://10.0.0.2/mcp"),
    )
    assert classify_locality(mixed) == "remote"
    assert classify_locality(TargetSpec(name="t", description="nothing to reach")) == "local"


# ===================================================================================================== authorisation
def test_safe_tests_need_no_authorisation_anywhere() -> None:
    for url in (LOCAL, PRIVATE, REMOTE):
        decision = gate(spec(url)).decide(case())
        assert decision.allowed and decision.risk == RiskClass.SAFE


def test_a_controlled_test_is_allowed_by_default_and_withdrawn_when_the_owner_withholds_it() -> None:
    controlled = case(risk_level=RiskClass.CONTROLLED)
    assert gate(spec()).decide(controlled).allowed
    refused = gate(spec(authorized_risk_classes=[RiskClass.SAFE])).decide(controlled)
    assert refused.blocked and refused.block_kind == "policy"
    assert "never inferred from a URL" in (refused.blocked_reason or "")


def test_a_high_impact_test_needs_the_owner_to_say_so_in_words() -> None:
    risky = case(risk_level=RiskClass.HIGH_IMPACT)
    everything = [RiskClass.SAFE, RiskClass.CONTROLLED, RiskClass.HIGH_IMPACT]
    assert gate(spec()).decide(risky).blocked, "not authorised by default"
    no_note = gate(spec(authorized_risk_classes=everything)).decide(risky)
    assert no_note.blocked and "written authorisation note" in (no_note.blocked_reason or "")
    assert (
        gate(spec(authorized_risk_classes=everything, authorization_note="staging copy, owner: ops"))
        .decide(risky)
        .allowed
    )


def test_high_impact_tests_against_a_remote_target_also_need_a_disposable_environment() -> None:
    risky = case(risk_level=RiskClass.HIGH_IMPACT)
    base = {
        "authorized_risk_classes": [RiskClass.SAFE, RiskClass.CONTROLLED, RiskClass.HIGH_IMPACT],
        "authorization_note": "owner approved on 2026-01-01",
    }
    refused = gate(spec(REMOTE, **base)).decide(risky)
    assert refused.blocked and "disposable" in (refused.blocked_reason or "")
    assert gate(spec(REMOTE, **base, disposable_environment=True)).decide(risky).allowed


def test_nothing_high_impact_ever_runs_against_production_whatever_is_authorised() -> None:
    everything = {
        "authorized_risk_classes": [RiskClass.SAFE, RiskClass.CONTROLLED, RiskClass.HIGH_IMPACT],
        "authorization_note": "approved",
        "disposable_environment": True,
        "production": True,
    }
    for url in (LOCAL, REMOTE):
        decision = gate(spec(url, **everything), production_allowed=True).decide(case(risk_level=RiskClass.HIGH_IMPACT))
        assert decision.blocked and "never run against a production target" in (decision.blocked_reason or "")


def test_a_production_target_is_only_touched_by_safe_tests_unless_the_installation_allows_more() -> None:
    production = spec(REMOTE, production=True)
    assert gate(production).decide(case()).allowed
    controlled = case(risk_level=RiskClass.CONTROLLED)
    assert "security.allow_production_targets is false" in (gate(production).decide(controlled).blocked_reason or "")
    assert gate(production, production_allowed=True).decide(controlled).allowed


def test_a_test_cannot_claim_to_be_safer_than_what_it_does() -> None:
    """The class a test *declares* is a floor. What it does (real side effects through a dangerous tool, a login, a
    workspace) raises it, and the gate acts on the higher of the two."""
    declared_safe = {"risk_level": RiskClass.SAFE}
    emails = case(context={"tools_under_test": ["send_email"], "real_side_effects": True}, **declared_safe)
    assert gate(spec()).classify(emails) == RiskClass.HIGH_IMPACT
    assert gate(spec()).decide(emails).blocked
    pays = case(expected_tool_calls=[ExpectedToolCall(name="transfer_funds")], context={"real_side_effects": True})
    assert gate(spec()).classify(pays) == RiskClass.HIGH_IMPACT
    simulated = case(context={"tools_under_test": ["send_email"], "real_side_effects": False})
    assert gate(spec()).classify(simulated) == RiskClass.SAFE, "a simulated tool is not a side effect"
    assert gate(spec()).classify(case(required_credentials=["staff"])) == RiskClass.CONTROLLED
    assert gate(spec()).classify(case(category="coding")) == RiskClass.CONTROLLED
    assert gate(spec()).classify(case(preconditions=["repository_execution"])) == RiskClass.CONTROLLED
    assert gate(spec()).classify(case(context={"workspace": {"fixture": "x"}})) == RiskClass.CONTROLLED


def test_a_test_that_declares_a_higher_class_keeps_it() -> None:
    assert gate(spec()).classify(case(risk_level=RiskClass.HIGH_IMPACT)) == RiskClass.HIGH_IMPACT


@pytest.mark.parametrize("category", ["security", "safety", "adversarial", "Security"])
def test_adversarial_tests_against_someone_elses_host_need_the_owners_attestation(category: str) -> None:
    attack = case(category)
    refused = gate(spec(REMOTE)).decide(attack)
    assert refused.blocked and refused.block_kind == "policy"
    assert "never attacks arbitrary infrastructure" in (refused.blocked_reason or "")
    allowed = gate(spec(REMOTE, authorization_note="I own this service; pentest approved")).decide(attack)
    assert allowed.allowed and any("owner's attestation" in r for r in allowed.reasons)


@pytest.mark.parametrize("url", [LOCAL, PRIVATE])
def test_adversarial_tests_against_the_owners_own_machine_or_network_run_without_a_ceremony(url: str) -> None:
    assert gate(spec(url)).decide(case("security")).allowed


def test_a_whitespace_note_is_not_an_attestation() -> None:
    assert gate(spec(REMOTE, authorization_note="   \n")).decide(case("security")).blocked


def test_adversarial_tests_against_a_production_target_are_withdrawn_before_the_attestation_is_even_read() -> None:
    production = spec(REMOTE, production=True, authorization_note="approved")
    decision = gate(production).decide(case("security"))
    assert decision.blocked and decision.risk == RiskClass.CONTROLLED
    assert "production" in (decision.blocked_reason or "")


# ============================================================================================== prerequisites
def test_a_missing_prerequisite_is_a_block_of_its_own_kind_and_says_how_to_fix_it() -> None:
    api_only = gate(spec(), judge_available=False, sandbox_available=False, browser_available=False)
    cases = {
        "required interface 'web' is not configured": case(required_interfaces=["web"]),
        "browser engine is unavailable": case(browser_steps=[{"action": "goto", "target": "/"}]),  # type: ignore[list-item]
        "credential profile 'staff' was not provided": case(required_credentials=["staff"]),
        "Docker is unavailable": case(preconditions=["sandbox"]),
        "an independent LLM judge is required": case(preconditions=["judge"]),
    }
    for expected, test in cases.items():
        decision = api_only.decide(test)
        assert (
            decision.blocked and decision.block_kind == "prerequisite" and expected in (decision.blocked_reason or "")
        ), expected
    assert "fails closed instead of executing on the host" in (
        api_only.decide(cases["Docker is unavailable"]).blocked_reason or ""
    )


def test_a_prerequisite_that_is_met_lets_the_test_through() -> None:
    creds = CredentialManager(redactor=SecretRedactor())
    creds.add(CredentialProfile(name="staff", kind="bearer"), {"token": "tok-" + "abcdef123456"})
    ready = gate(spec(), credentials=creds, judge_available=True, sandbox_available=True, browser_available=True)
    for test in (
        case(required_credentials=["staff"]),
        case(preconditions=["judge"]),
        case(preconditions=["sandbox"]),
        case(browser_steps=[{"action": "goto", "target": "/"}]),  # type: ignore[list-item]
    ):
        assert ready.decide(test).allowed, test


def test_a_target_with_nothing_to_talk_to_blocks_dynamic_tests_but_not_static_analysis() -> None:
    nothing = gate(TargetSpec(name="repo-only", description="only a repository"))
    blocked = nothing.decide(case())
    assert blocked.blocked and "no running instance" in (blocked.blocked_reason or "")
    assert nothing.decide(case(context={"engine": "static"})).allowed


def test_an_interface_that_was_declared_but_cannot_be_reached_is_unavailable_not_unconfigured() -> None:
    down = gate(spec(), interfaces=[], unavailable={"api": "All connection attempts failed"})
    asked_for_api = down.decide(case(required_interfaces=["api"]))
    assert asked_for_api.blocked and "unavailable: All connection attempts failed" in (
        asked_for_api.blocked_reason or ""
    )
    anything = down.decide(case())
    assert "nothing can be sent to it" in (anything.blocked_reason or "")
    assert down.decide(case(context={"engine": "static"})).allowed


def test_prerequisites_are_reported_before_authorisation_so_the_user_fixes_the_right_thing_first() -> None:
    both = gate(spec(authorized_risk_classes=[RiskClass.SAFE])).decide(
        case(risk_level=RiskClass.HIGH_IMPACT, required_credentials=["staff"])
    )
    assert both.block_kind == "prerequisite"


def test_every_decision_says_what_the_test_was_classified_as_and_where() -> None:
    decision = gate(spec(PRIVATE)).decide(case())
    assert decision.allowed and any("classified safe; locality=private" in r for r in decision.reasons)
