"""The suites and intensities are named in four places: the server's lists, the types a request is checked against, the
OpenAPI document and the web wizard's choices. The wizard once offered "deep" while the server knew "thorough", so a person
who picked it got a failed run. These tests hold the Python side to one list; the web side is held to the generated types
by the type checker (``npm run build``)."""

from __future__ import annotations

from typing import get_args

import pytest
from pydantic import ValidationError

from agentlab.core.enums import Intensity, Suite, SuiteKind
from agentlab.core.errors import UserError
from agentlab.design import SUITES, check_suite
from agentlab.jobs.models import JobOptions
from agentlab.orchestrator.options import RunOptions
from agentlab.skills.context import INTENSITIES, check_intensity


def test_every_suite_is_named_once_everywhere() -> None:
    assert set(get_args(Suite)) == set(SUITES) == {s.value for s in SuiteKind}


def test_the_intensity_list_is_the_type() -> None:
    assert INTENSITIES == get_args(Intensity) == ("quick", "standard", "thorough")


@pytest.mark.parametrize("value", INTENSITIES)
def test_a_job_accepts_every_intensity_there_is(value: str) -> None:
    assert JobOptions(intensity=value).intensity == value  # type: ignore[arg-type]


@pytest.mark.parametrize("value", sorted(SUITES))
def test_a_job_accepts_every_suite_there_is(value: str) -> None:
    assert JobOptions(suite=value).suite == value  # type: ignore[arg-type]


@pytest.mark.parametrize("field,value", [("intensity", "deep"), ("suite", "everything")])
def test_a_job_refuses_a_name_that_does_not_exist(field: str, value: str) -> None:
    with pytest.raises(ValidationError) as caught:
        JobOptions(**{field: value})
    assert field in str(caught.value) and value in str(caught.value)


def test_the_checks_name_what_exists() -> None:
    with pytest.raises(UserError, match=r"unknown intensity 'deep' \(known: quick, standard, thorough\)"):
        check_intensity("deep")
    with pytest.raises(UserError, match=r"unknown suite 'nope' \(known: discovery, functional"):
        check_suite("nope")
    assert check_intensity("thorough") == "thorough" and check_suite("reliability") == "reliability"


def test_a_job_becomes_the_run_options_it_describes() -> None:
    from agentlab.core.models import TargetSpec
    from agentlab.jobs.models import JobSpec

    job = JobSpec(
        kind="run",
        run_id="r1",
        target=TargetSpec(name="x"),
        options=JobOptions(suite="reliability", intensity="thorough"),
    )
    options: RunOptions = job.run_options()
    assert (options.suite, options.intensity) == ("reliability", "thorough")
