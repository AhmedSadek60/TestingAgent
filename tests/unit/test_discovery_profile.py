"""What the profile says about the agent comes from what was given or found, and says which."""

from __future__ import annotations

from agentlab.core.models import TargetSpec
from agentlab.core.models.target import LlmTargetConfig
from agentlab.discovery.fingerprint import FingerprintInputs, build_profile


def models_of(spec: TargetSpec) -> list[str]:
    return build_profile(FingerprintInputs(spec=spec, available_interfaces=spec.interfaces())).models


def test_the_model_an_llm_target_names_is_the_model_under_test() -> None:
    spec = TargetSpec(name="m", llm=LlmTargetConfig(provider="ollama", model="qwen2.5:0.5b"))
    assert models_of(spec) == ["ollama:qwen2.5:0.5b"]


def test_a_provider_with_no_model_named_is_listed_by_provider() -> None:
    assert models_of(TargetSpec(name="m", llm=LlmTargetConfig(provider="local"))) == ["local"]


def test_a_target_that_names_no_model_and_has_no_repository_claims_none() -> None:
    assert models_of(TargetSpec(name="m", description="A support bot.")) == []
