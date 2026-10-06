"""The tests a skill's templates describe are checked when the skill is loaded.

``agentlab skills validate`` used to say "ok" for a template whose assertions were written ``{type: contains, value: x}``
(the settings belong under ``params``), and the planner then dropped the whole skill with a warning, so a custom skill
quietly produced no tests. These tests pin the other behaviour: what ``validate`` accepts is what the planner turns into
tests, and what it cannot turn into tests is reported with the fix.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from agentlab.skills import IdAllocator, SkillRegistry
from agentlab.skills.loader import load_skill_dir
from agentlab.skills.model import REQUIRED_DOC_SECTIONS, Skill
from tests.support.profiles import make_ctx


def make_skill(tmp_path: Path, *templates: dict[str, Any], name: str = "refund-checks") -> Skill:
    folder = tmp_path / name
    folder.mkdir()
    manifest = {
        "name": name,
        "version": "0.1.0",
        "title": "Refund checks",
        "description": "Checks the refund answers.",
        "id_prefix": "RFD",
        "applicability": {"always": True},
        "templates": list(templates),
    }
    (folder / "skill.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    sections = "\n".join(f"## {s}\n\ntext\n" for s in REQUIRED_DOC_SECTIONS)
    (folder / "SKILL.md").write_text(f"# {name}\n\n{sections}", encoding="utf-8")
    return load_skill_dir(folder, trust="local")


def template(assertions: list[Any], **test: Any) -> dict[str, Any]:
    return {
        "id": "WINDOW",
        "test": {
            "name": "Window",
            "objective": "States the window",
            "input": "How long?",
            "assertions": assertions,
            **test,
        },
        "reasons": ["The window is the most asked question."],
    }


def generated(skill: Skill):
    registry = SkillRegistry()
    registry.add(skill)
    return registry.generate(skill, make_ctx(), IdAllocator())


def test_assertion_settings_written_beside_type_are_reported_with_the_fix(tmp_path: Path) -> None:
    skill = make_skill(tmp_path, template([{"type": "contains", "value": "30"}]))
    [problem] = skill.problems
    assert "assertions[1]" in problem and "unknown keys ['value']" in problem
    assert "params" in problem, "the message names the fix"
    assert not skill.usable


def test_what_validate_accepts_is_what_the_planner_turns_into_tests(tmp_path: Path) -> None:
    skill = make_skill(tmp_path, template([{"type": "contains", "params": {"value": "30"}}, {"type": "no_error"}]))
    assert skill.problems == []
    run = generated(skill)
    [draft] = run.drafts
    assert [(a.type, a.params) for a in draft.test.assertions] == [("contains", {"value": "30"}), ("no_error", {})]
    assert run.notes == []


def test_an_unknown_assertion_type_is_reported(tmp_path: Path) -> None:
    skill = make_skill(tmp_path, template([{"type": "containz", "params": {"value": "30"}}]))
    assert any("unknown assertion type 'containz'" in p for p in skill.problems)


def test_an_assertion_without_a_type_or_with_params_that_are_not_a_mapping_is_reported(tmp_path: Path) -> None:
    skill = make_skill(tmp_path, template([{"params": {"value": "30"}}, {"type": "contains", "params": "30"}]))
    assert any("assertions[1]: missing 'type'" in p for p in skill.problems)
    assert any("assertions[2]: 'params' must be a mapping" in p for p in skill.problems)


def test_a_wrong_value_in_a_known_key_is_reported_before_a_run(tmp_path: Path) -> None:
    skill = make_skill(tmp_path, template([{"type": "no_error", "weight": "heavy"}]))
    assert any("assertions[1]: weight" in p for p in skill.problems), skill.problems


def test_the_other_nested_parts_of_a_test_are_checked_too(tmp_path: Path) -> None:
    skill = make_skill(
        tmp_path,
        template(
            [{"type": "no_error"}],
            turns=[{"input": "hello", "assertions": [{"type": "contains", "value": "x"}], "typo": 1}],
            judge=[{"metric": "politeness"}],
            expected_tool_calls=[{"name": "lookup", "args": {}}],
            browser_steps=[{"action": "teleport"}],
        ),
    )
    text = "\n".join(skill.problems)
    assert "turns[1]: unknown keys ['typo']" in text
    assert "turns[1].assertions[1]: unknown keys ['value']" in text
    assert "judge[1]: rubric" in text, "a criterion needs its rubric"
    assert "expected_tool_calls[1]: unknown keys ['args']" in text
    assert "browser_steps[1]: action" in text


def test_a_list_that_is_not_a_list_is_reported(tmp_path: Path) -> None:
    skill = make_skill(tmp_path, template({"type": "no_error"}))  # type: ignore[arg-type]
    assert any("'assertions' must be a list" in p for p in skill.problems)


def test_placeholders_are_judged_only_once_they_are_rendered(tmp_path: Path) -> None:
    skill = make_skill(
        tmp_path,
        template(
            [
                {"type": "contains", "params": {"value": "[[ item.name ]]"}},
                {"type": "[[ 'no_error' ]]"},
                "[[ item.assertion ]]",
                {"type": "no_error", "weight": "[[ 1 ]]"},
            ]
        ),
    )
    assert skill.problems == []


def test_fields_agentlab_fills_in_itself_cannot_be_set_by_a_template(tmp_path: Path) -> None:
    skill = make_skill(tmp_path, template([{"type": "no_error"}], id="MINE", rationale="because"))
    text = "\n".join(skill.problems)
    assert "'id' cannot be set by a template" in text and "'rationale' cannot be set" in text


def test_a_template_that_renders_to_an_invalid_test_does_not_discard_the_others(tmp_path: Path) -> None:
    broken = {**template([{"type": "no_error"}], timeout="[[ 'soon' ]]"), "id": "BROKEN"}
    fine = {**template([{"type": "no_error"}]), "id": "FINE"}
    skill = make_skill(tmp_path, broken, fine)
    assert skill.problems == [], "only the rendered value is wrong, so it cannot be known at load time"
    run = generated(skill)
    assert [d.test.id for d in run.drafts] == ["RFD-FINE-001"]
    assert any("template 'BROKEN' skipped" in n and "invalid test" in n for n in run.notes), run.notes
