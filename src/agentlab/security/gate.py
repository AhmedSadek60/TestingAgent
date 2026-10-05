"""Safety / authorisation gate (spec sections 35 and 36).

Every test is classified SAFE, CONTROLLED or HIGH_IMPACT *before* execution and then either
allowed or **blocked**. A blocked test is reported separately from failures. The gate never
infers authorisation from possession of a URL: elevated classes need an explicit statement in
the target's :class:`SafetyPolicy`, and adversarial tests against a remote host additionally need
the owner's attestation. Locality (loopback / private network / in-process mock / sandboxed
command) is treated as owner-controlled infrastructure.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from urllib.parse import urlparse

from agentlab.core.config import AgentLabConfig
from agentlab.core.enums import RiskClass
from agentlab.core.models import TargetSpec, TestCase
from agentlab.security.credentials import CredentialManager

ADVERSARIAL_CATEGORIES = {"security", "safety", "adversarial"}
HIGH_IMPACT_TOOLS = {"send_email", "transfer_funds", "delete_file", "delete_record", "execute_payment", "post_message",
                     "create_user", "delete_user", "run_shell", "execute_code"}
RISK_ORDER = {RiskClass.SAFE: 0, RiskClass.CONTROLLED: 1, RiskClass.HIGH_IMPACT: 2}


@dataclass
class GateDecision:
    allowed: bool
    risk: RiskClass
    reasons: list[str] = field(default_factory=list)
    block_kind: str | None = None  # "policy" | "prerequisite"
    blocked_reason: str | None = None

    @property
    def blocked(self) -> bool:
        return not self.allowed


def _host_kind(host: str) -> str:
    h = host.lower()
    if h in {"localhost", "host.docker.internal", ""} or h.endswith(".localhost"):
        return "local"
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return "private" if h.endswith((".local", ".internal", ".lan", ".home.arpa")) or "." not in h else "remote"
    if ip.is_loopback:
        return "local"
    return "private" if (ip.is_private or ip.is_link_local) else "remote"


def classify_locality(spec: TargetSpec) -> str:
    """``local`` (in-process, loopback, sandbox), ``private`` (RFC1918/ULA/.local) or ``remote``."""
    rank = {"local": 0, "private": 1, "remote": 2}
    worst = "local"  # mock, llm, command (sandboxed) or repository-only targets have no remote host
    for url in (spec.api.url if spec.api else None, spec.web.url if spec.web else None,
                spec.mcp.url if spec.mcp else None):
        if url:
            kind = _host_kind(urlparse(url).hostname or "")
            if rank[kind] > rank[worst]:
                worst = kind
    return worst


class AuthorizationGate:
    def __init__(self, spec: TargetSpec, config: AgentLabConfig, *, credentials: CredentialManager | None = None,
                 interfaces: list[str] | None = None, sandbox_available: bool = False,
                 judge_available: bool = False, browser_available: bool = False) -> None:
        self.spec = spec
        self.config = config
        self.credentials = credentials
        self.interfaces = set(interfaces if interfaces is not None else spec.interfaces())
        self.sandbox_available = sandbox_available
        self.judge_available = judge_available
        self.browser_available = browser_available
        self.locality = classify_locality(spec)

    # ----------------------------------------------------------------- classification
    def classify(self, test: TestCase) -> RiskClass:
        """The effective class is the highest of the declared class and what the test content implies."""
        risk = test.risk_level
        implied = RiskClass.SAFE
        if test.required_credentials or test.browser_steps and self.spec.web and self.spec.web.auth_credential:
            implied = RiskClass.CONTROLLED
        if test.context.get("workspace") or "repository_execution" in test.preconditions or \
                test.category.lower() in {"coding"}:
            implied = RiskClass.CONTROLLED
        tools = {c.name for c in test.expected_tool_calls} | set(test.context.get("tools_under_test", []))
        if tools & HIGH_IMPACT_TOOLS and test.context.get("real_side_effects", False):
            implied = RiskClass.HIGH_IMPACT
        if self.spec.safety.production and self.locality == "remote" and test.category.lower() in ADVERSARIAL_CATEGORIES:
            implied = max(implied, RiskClass.CONTROLLED, key=RISK_ORDER.get)  # type: ignore[arg-type]
        return max(risk, implied, key=RISK_ORDER.get)  # type: ignore[arg-type]

    # ----------------------------------------------------------------- decision
    def decide(self, test: TestCase) -> GateDecision:
        risk = self.classify(test)
        safety = self.spec.safety
        d = GateDecision(allowed=True, risk=risk)

        def block(kind: str, reason: str) -> GateDecision:
            d.allowed, d.block_kind, d.blocked_reason = False, kind, reason
            d.reasons.append(reason)
            return d

        # ---- prerequisites (BLOCKED, never FAILED)
        for iface in test.required_interfaces:
            if iface not in self.interfaces:
                return block("prerequisite", f"required interface '{iface}' is not configured for this target "
                                             f"(available: {sorted(self.interfaces) or 'none'})")
        if self.spec.interfaces() == [] and not self.interfaces:
            return block("prerequisite", "the target exposes no interface that AgentLab can drive")
        if test.browser_steps and not self.browser_available:
            return block("prerequisite", "browser engine is unavailable (Playwright/Chromium not installed or disabled)")
        for cred in test.required_credentials:
            if self.credentials is None or not self.credentials.has(cred):
                return block("prerequisite", f"credential profile '{cred}' was not provided; authenticated test skipped")
        if ("sandbox" in test.preconditions or test.context.get("workspace")) and not self.sandbox_available:
            return block("prerequisite", "an isolated sandbox is required for this test but Docker is unavailable; "
                                         "AgentLab fails closed instead of executing on the host")
        if "judge" in test.preconditions and not self.judge_available:
            return block("prerequisite", "an independent LLM judge is required but none is configured")

        # ---- authorisation
        if risk not in set(safety.authorized_risk_classes) and risk != RiskClass.SAFE:
            return block("policy", f"{risk.value.upper()} tests are not authorised for this target; grant them explicitly in "
                                   f"the target's safety.authorized_risk_classes (authorisation is never inferred from a URL)")
        if risk == RiskClass.HIGH_IMPACT:
            if not (safety.authorization_note or "").strip():
                return block("policy", "HIGH_IMPACT tests need a written authorisation note (safety.authorization_note)")
            if self.locality == "remote" and not safety.disposable_environment:
                return block("policy", "HIGH_IMPACT tests against a remote target require safety.disposable_environment=true "
                                       "(a disposable / test environment)")
        if safety.production and risk != RiskClass.SAFE and not self.config.security.allow_production_targets:
            return block("policy", "target is marked as production and security.allow_production_targets is false")
        if safety.production and risk == RiskClass.HIGH_IMPACT:
            return block("policy", "HIGH_IMPACT tests are never run against a production target")
        if test.category.lower() in ADVERSARIAL_CATEGORIES and self.locality == "remote":
            if not (safety.authorization_note or "").strip():
                return block("policy", "adversarial security tests against a remote target require the owner's authorisation "
                                       "attestation (safety.authorization_note); AgentLab never attacks arbitrary infrastructure")
            d.reasons.append("adversarial tests on a remote target allowed by the owner's attestation")
        d.reasons.append(f"classified {risk.value}; locality={self.locality}")
        return d
