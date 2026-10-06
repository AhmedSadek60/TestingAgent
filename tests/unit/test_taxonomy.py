"""Which tests count as security probes, and for which of the 28 categories. A test that only asks the agent to do a task
is not an attack, and counting its failure as one ("attack succeeded") would make the security picture wrong both ways."""

from __future__ import annotations

import pytest

from agentlab.core.models import TestCase
from agentlab.design.taxonomy import letters_for, security_codes


def case(test_id: str, category: str = "browser") -> TestCase:
    return TestCase(id=test_id, name=test_id, category=category, objective=test_id)


@pytest.mark.parametrize(
    "test_id,codes",
    [
        ("BROW-HIDDEN-INSTRUCTION-001", ["N7", "N26"]),  # an instruction hidden in the page
        ("BROW-LOOKALIKE-LOGIN-001", ["N7"]),  # a lookalike page that asks for the credentials again
        ("BROW-DESTRUCTIVE-BUTTON-001", ["N18"]),  # a hazard on the page, not an instruction delivered by it
        ("BROW-STEP-BOUND-001", ["N24"]),
        ("BROW-ADD-TO-CART-001", []),  # a task the agent should complete
        ("BROW-COOKIE-BANNER-001", []),
    ],
)
def test_the_browser_agent_tests_are_counted_for_what_they_probe(test_id: str, codes: list[str]) -> None:
    assert security_codes(case(test_id)) == codes


def test_a_task_in_a_browser_is_not_adversarial_so_it_does_not_count_for_n() -> None:
    assert letters_for(case("BROW-ADD-TO-CART-001"), ["J", "N"]) == ["J"]
    assert letters_for(case("BROW-HIDDEN-INSTRUCTION-001"), ["J", "N"]) == ["N", "J"]
