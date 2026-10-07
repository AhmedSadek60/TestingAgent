"""YAML from outside is read through ``agentlab.security.safeyaml``: an alias bomb (a few hundred bytes that describe billions
of values) used to make the document parser exhaust memory, after eight seconds, in a process that was only asked to read a
file."""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest
import yaml

from agentlab.core.errors import ParserError
from agentlab.documents.parsers import DOCUMENT_PARSERS, _flatten
from agentlab.security import safeyaml
from agentlab.skills.loader import load_skill_dir

SRC = Path(__file__).resolve().parents[2] / "src" / "agentlab"


def bomb(levels: int = 9, fan_out: int = 9) -> str:
    """``levels`` lists, each holding ``fan_out`` references to the one before: 9**9 values in under 400 bytes."""
    lines = ["a0: &a0 [lol, lol, lol, lol, lol, lol, lol, lol, lol]"]
    for i in range(1, levels):
        refs = ", ".join([f"*a{i - 1}"] * fan_out)
        lines.append(f"a{i}: &a{i} [{refs}]")
    return "\n".join(lines) + "\n"


def took(fn, *args, **kwargs) -> tuple[float, object]:  # type: ignore[no-untyped-def]
    started = time.perf_counter()
    try:
        return time.perf_counter() - started, fn(*args, **kwargs)
    except Exception as exc:  # the caller wants the elapsed time of a refusal as well
        return time.perf_counter() - started, exc


# ----------------------------------------------------------------------------------------------------- reading
@pytest.mark.parametrize(
    "text",
    [
        "",
        "plain",
        "- a\n- b: 1\n  c: [1, 2, 3]\n",
        "name: unicode — ünï\nlist: [1, 2.5, true, null, 2026-10-06]\n",
        "base: &b {x: 1, y: 2}\nuse:\n  <<: *b\n  y: 3\nagain: *b\n",
        "text: |\n  multi\n  line\nfolded: >\n  one\n  two\n",
        "shared: &s [1, 2, 3]\nrows: [*s, *s, *s, *s]\n",
        "{json: [1, 2, {a: b}]}",
    ],
)
def test_an_ordinary_document_reads_exactly_as_yaml_safe_load_reads_it(text: str) -> None:
    assert safeyaml.load(text) == yaml.safe_load(text)


def test_bytes_are_decoded_as_utf8() -> None:
    assert safeyaml.load("é: 1".encode()) == {"é": 1}


def test_syntax_errors_and_several_documents_are_the_same_yaml_errors_as_before() -> None:
    with pytest.raises(yaml.YAMLError):
        safeyaml.load("a: [unclosed")
    with pytest.raises(yaml.YAMLError):
        safeyaml.load("a: 1\n---\nb: 2\n")


def test_tags_that_would_build_objects_are_still_refused() -> None:
    with pytest.raises(yaml.YAMLError):
        safeyaml.load("x: !!python/object/apply:os.system ['true']\n")


# ---------------------------------------------------------------------------------------------------- refusing
def test_an_alias_bomb_is_refused_at_once_and_says_why() -> None:
    text = bomb()
    assert len(text) < 500
    elapsed, outcome = took(safeyaml.load, text)
    assert isinstance(outcome, safeyaml.YamlRefused) and "alias" in str(outcome)
    assert isinstance(outcome, yaml.YAMLError), "existing `except yaml.YAMLError` handlers keep working"
    assert elapsed < 2, f"counting the expansion must not expand it (took {elapsed:.1f}s)"


def test_the_bound_is_what_the_text_could_hold_so_modest_re_use_is_fine() -> None:
    text = bomb(levels=3, fan_out=9)  # 9**3 = 729 values from ~150 characters
    assert len(safeyaml.load(text)["a2"]) == 9


def test_an_alias_that_refers_to_the_structure_containing_it_is_refused() -> None:
    for text in ("&a [*a]", "&m {k: *m}", "x: &x\n  y: [*x]\n"):
        with pytest.raises(safeyaml.YamlRefused, match="contains it"):
            safeyaml.load(text)


def test_too_long_and_too_deeply_nested_documents_are_refused() -> None:
    with pytest.raises(safeyaml.YamlRefused, match="characters"):
        safeyaml.load("a: " + "x" * 200, max_chars=100)
    with pytest.raises(safeyaml.YamlRefused, match="deeply"):
        safeyaml.load("[" * 20_000 + "]" * 20_000)


def test_expanded_size_counts_values_without_building_them() -> None:
    root = yaml.compose("a: &x [1, 2]\nb: [*x, *x, *x]\n", Loader=yaml.SafeLoader)
    # the mapping (1) + key a (1) + [1, 2] (1 + 2) + key b (1) + three references to [1, 2] (1 + 3 * 3)
    assert safeyaml.expanded_size(root, 10_000) == 1 + 1 + 3 + 1 + 10


# ---------------------------------------------------------------------------------------- where it is used
def test_a_yaml_document_that_is_a_bomb_is_a_parser_error_not_an_exhausted_process() -> None:
    parse = DOCUMENT_PARSERS.get(".yaml")
    elapsed, outcome = took(parse, bomb().encode(), "evil.yaml")
    assert isinstance(outcome, ParserError) and "not read" in str(outcome)
    assert elapsed < 2


def test_markdown_front_matter_that_is_a_bomb_is_a_warning_and_the_body_is_still_read() -> None:
    parsed = DOCUMENT_PARSERS.get(".md")(f"---\n{bomb()}---\n# Title\n\nbody text\n".encode(), "evil.md")
    assert any("front matter was not read" in w for w in parsed.warnings)
    assert any(b.text == "body text" for b in parsed.blocks)


def test_a_skill_manifest_that_is_a_bomb_is_an_invalid_skill_not_a_hang(tmp_path: Path) -> None:
    (tmp_path / "skill.yaml").write_text(bomb(), encoding="utf-8")
    elapsed, skill = took(load_skill_dir, tmp_path, trust="imported", require_doc=False)
    assert elapsed < 2 and not isinstance(skill, Exception)
    assert skill.problems and "alias" in skill.problems[0]  # type: ignore[attr-defined]


def test_the_walk_over_a_parsed_document_is_bounded_in_work_depth_and_output() -> None:
    wide = {f"k{i}": [f"value-{i}-{j}" for j in range(500)] for i in range(2000)}  # a million leaves
    elapsed, flat = took(_flatten, wide)
    assert len(flat) <= 3000 and elapsed < 2  # type: ignore[arg-type]

    deep: object = "leaf-value"
    for _ in range(5000):
        deep = {"n": deep}
    flat = _flatten(deep)  # no recursion error
    assert flat and flat[-1][1] == "(nested too deeply to read)"

    loop: list[object] = []
    loop.append(
        loop
    )  # a Python object that contains itself can only come from outside the parser, but the walk survives it
    assert _flatten(loop)


def test_json_nested_beyond_the_parsers_limit_is_a_parser_error() -> None:
    with pytest.raises(ParserError, match="invalid JSON"):
        DOCUMENT_PARSERS.get(".json")(b"[" * 50_000 + b"]" * 50_000, "deep.json")


# ------------------------------------------------------------------------------------- the one entry point
def test_no_module_reads_yaml_except_through_safeyaml() -> None:
    """The guard is the point: a new ``yaml.safe_load`` somewhere would silently bring the bomb back."""
    direct = re.compile(r"\byaml\.(safe_load|safe_load_all|load|load_all|full_load|unsafe_load)\s*\(")
    offenders = [
        f"{path.relative_to(SRC)}:{n}"
        for path in sorted(SRC.rglob("*.py"))
        if path.name != "safeyaml.py" and "static" not in path.parts
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if direct.search(line) and not line.lstrip().startswith("#")
    ]
    assert not offenders, "read YAML with agentlab.security.safeyaml.load: " + ", ".join(offenders)
