"""The run manifest records what a run measured; comparing two manifests says whether the runs are comparable."""

from __future__ import annotations

import copy

from agentlab.core.config import AgentLabConfig, EvaluationConfig, JudgeConfig, ProviderConfig
from agentlab.orchestrator.manifest import comparable, compare_manifests, config_fingerprint

BASE = {
    "agentlab_version": "0.1.0",
    "target": {"name": "t", "version": "1", "spec_hash": "aaa"},
    "plan": {"hash": "p1", "intensity": "standard", "suite": "full"},
    "scoring_profile": {"name": "general", "hash": "h1"},
    "skills": [{"name": "rag-testing", "version": "1.0.0", "content_hash": "c1"}],
    "judges": {"enabled": True, "judges": [{"provider": "j", "model": "m"}]},
    "config": {"evaluation": {"repetitions": 1}, "limits": {"max_cost_usd": 10}},
    "options": {"second_wave": True, "intensity": "standard"},
    "environment": {"docker": True, "browser": False},
}


def changed(**edits):  # type: ignore[no-untyped-def]
    m = copy.deepcopy(BASE)
    for path, value in edits.items():
        cur = m
        *parents, last = path.split("__")
        for part in parents:
            cur = cur[part]
        cur[last] = value
    return m


def test_identical_manifests_have_no_differences() -> None:
    assert compare_manifests(BASE, copy.deepcopy(BASE)) == []


def test_changed_scoring_profile_blocks_comparison_of_overall_scores() -> None:
    diffs = compare_manifests(BASE, changed(scoring_profile={"name": "safety_critical", "hash": "h2"}))
    assert not comparable(diffs) and diffs[0].field == "scoring_profile"


def test_changed_skill_or_judge_is_a_caveat_not_a_blocker() -> None:
    diffs = compare_manifests(
        BASE,
        changed(
            skills=[{"name": "rag-testing", "version": "1.1.0", "content_hash": "c2"}],
            judges={"enabled": True, "judges": [{"provider": "other", "model": "m"}]},
        ),
    )
    assert comparable(diffs)
    assert {d.field for d in diffs} == {"skills.rag-testing", "judges"}
    assert all(d.impact == "caveat" for d in diffs)


def test_a_changed_target_is_informational_for_regression() -> None:
    diffs = compare_manifests(BASE, changed(target__spec_hash="bbb", target__version="2"))
    assert {d.impact for d in diffs} == {"info"} and comparable(diffs)


def test_major_version_change_blocks_but_patch_change_does_not() -> None:
    assert not comparable(compare_manifests(BASE, changed(agentlab_version="1.0.0")))
    patch = compare_manifests(BASE, changed(agentlab_version="0.1.3"))
    assert comparable(patch) and patch[0].impact == "info"


def test_environment_difference_explains_blocked_tests() -> None:
    diffs = compare_manifests(BASE, changed(environment__docker=False))
    assert diffs[0].field == "environment.docker" and "BLOCKED" in diffs[0].note


def test_config_fingerprint_never_contains_secrets() -> None:
    cfg = AgentLabConfig(
        providers=[
            ProviderConfig(
                name="p",
                type="openai",
                model="m",
                api_key_ref="env:OPENAI_API_KEY",
                headers={"Authorization": "Bearer sk-live-SECRET"},
            )
        ],
        evaluation=EvaluationConfig(judges=[JudgeConfig(provider="p", model="m")]),
    )
    dumped = str(config_fingerprint(cfg))
    assert "SECRET" not in dumped and "OPENAI_API_KEY" not in dumped and "Authorization" not in dumped
    assert "'name': 'p'" in dumped
