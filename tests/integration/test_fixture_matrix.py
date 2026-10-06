"""The proof that AgentLab finds what was planted (spec section 23: "the test suite must prove that it can find the
planted defects").

For every fixture kind, against real HTTP services and the real orchestrator (no LLM, no network beyond loopback):

* the correct build passes the whole suite with no finding,
* each planted defect, alone, makes one of its expected tests fail with a finding of at least the expected severity,
* all defects together are caught and lower the score.

A failure here is a detector gap or a false alarm, never something to relax: fix the skill or the fixture.
"""

from __future__ import annotations

import pytest

from agentlab.fixtures import REGISTRY, fixture_class
from agentlab.fixtures.selftest import audit_expectation, load_expectation, verify_kind

KINDS = sorted(REGISTRY)


@pytest.mark.parametrize("kind", KINDS)
def test_every_defect_has_an_expectation_and_nothing_else_does(kind: str) -> None:
    assert audit_expectation(kind) == []


@pytest.mark.parametrize("kind", KINDS)
def test_expected_detectors_are_specific(kind: str) -> None:
    """An expectation that names every test would pass for any failure; each defect must name a few."""
    for name, expected in load_expectation(kind).defects.items():
        assert expected.detected_by, f"{kind}.{name} names no detecting test"
        assert "*" not in expected.detected_by, f"{kind}.{name} accepts any test"
        assert len(expected.detected_by) <= 12, f"{kind}.{name} names too many detectors to be a real expectation"


@pytest.mark.matrix
@pytest.mark.parametrize("kind", KINDS)
def test_agentlab_finds_the_planted_defects(kind: str) -> None:
    report = verify_kind(kind, workers=4)
    assert report.ok, f"{kind}:\n" + "\n".join(f"  {c.name}: {c.detail}" for c in report.failures)
    planted = len(fixture_class(kind).DEFECTS)
    assert len(report.checks) == planted + 2  # correct build, every defect, all defects together
