"""A target of a kind AgentLab has no code for, tested end to end through an adapter a plug-in provides (spec section 43).

The adapter is the whole of the plug-in: AgentLab's discovery, planning, execution, evaluation, scoring and reporting are
the ones every other target goes through, and none of them is told that this interface exists.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from agentlab.adapters.base import ADAPTERS, AdapterCapabilities, AdapterContext, AgentAdapter
from agentlab.core.errors import TargetError
from agentlab.core.models import AgentRequest, AgentResponse, TargetSpec
from tests.support.lab import Lab


class ShoutingAdapter(AgentAdapter):
    """Answers every message by shouting it back, with the prefix its settings name."""

    kind = "shouting-plugin"

    def __init__(self, spec: TargetSpec, ctx: AdapterContext) -> None:
        super().__init__(spec, ctx)
        if self.kind not in spec.custom:
            raise TargetError(f"the target has no '{self.kind}' settings")
        self.settings = spec.custom[self.kind]
        self.capabilities = AdapterCapabilities(notes=["a plug-in adapter"])
        self.closed = False

    async def send(self, request: AgentRequest) -> AgentResponse:
        return AgentResponse(output=f"{self.settings.get('prefix', '')}{request.input.upper()}")

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def installed() -> Any:
    ADAPTERS.register("shouting-plugin", ShoutingAdapter)
    yield
    ADAPTERS.unregister("shouting-plugin")


@pytest.fixture
async def lab(tmp_path: Path) -> AsyncIterator[Lab]:
    async with Lab(tmp_path / "lab", formats=["json"]) as lab:
        yield lab


TARGET = {
    "name": "shouter",
    "description": "A chat assistant that answers by shouting back what it hears",
    "custom": {"shouting-plugin": {"prefix": "HEARD: "}},
}


async def test_a_plug_in_adapter_target_goes_through_the_whole_pipeline(installed: None, lab: Lab) -> None:
    outcome = await lab.run(TARGET)
    summary = outcome.summary()
    assert outcome.status.value == "completed" and summary["tests"] > 20, summary
    assert summary["counts"].get("passed", 0) > 0 and summary["overall"] is not None
    assert summary["security_posture"] != "not_tested", "security tests ran against it too"

    results = lab.store.list_results(outcome.run_id)
    answered = [o for r in results for a in r.attempts for o in a.outputs]
    assert answered and all(o.startswith("HEARD: ") for o in answered), "every answer came through the plug-in adapter"

    manifest = lab.store.get_run(outcome.run_id)["manifest"]
    assert manifest["target"]["interfaces"] == ["shouting-plugin"], "the manifest records which interface was tested"

    report = json.loads(next((lab.root / "reports").glob("*/v1/report.json")).read_text(encoding="utf-8"))
    assert report["run"]["target"] == "shouter" and report["results"], "and the report is built as for any target"
    assert report["target"]["interfaces"] == report["environment"]["interfaces"] == ["shouting-plugin"]


async def test_without_the_plug_in_nothing_is_tested_and_the_run_says_so(lab: Lab) -> None:
    outcome = await lab.run(TARGET)
    summary = outcome.summary()
    assert summary["tests"] == 0 and summary["security_posture"] == "not_tested" and summary["grade"] is None
    assert any("interface 'shouting-plugin' is unavailable" in w for w in outcome.warnings), outcome.warnings
    assert any("unknown adapters plug-in 'shouting-plugin'" in w for w in outcome.warnings)
