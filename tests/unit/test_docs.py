"""The documentation says what the code does.

Documentation drifts the moment it is written: a command is renamed, a key is added, a check is removed, and the page
that names it stays as it was. Each test here reads the documents and compares what they claim with what is installed:
links and anchors, configuration keys and defaults, command-line commands and options, API routes, built-in skills,
assertions and security categories, environment variables, repository paths, exit codes, and the code and configuration
examples. A failure names the file and the line; either the document or the code is wrong.

Nothing here runs a command or talks to a service. What was run by hand is written down in ``docs/development.md``.
"""

from __future__ import annotations

import ast
import importlib
import json
import re
import shlex
import textwrap
import types
import typing
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import typer
from pydantic import BaseModel

from agentlab.api.app import create_app
from agentlab.cli import common as cli_common
from agentlab.cli.main import app as cli_app
from agentlab.core.config import AgentLabConfig
from agentlab.core.models.target import TargetSpec
from agentlab.core.models.testcase import AssertionSpec
from agentlab.design.taxonomy import SECURITY_CATEGORIES, TAXONOMY
from agentlab.design.user_tests import load_user_tests
from agentlab.evaluation.assertions import ASSERTIONS
from agentlab.evaluation.scoring import ScoringProfile
from agentlab.registries import all_registries, is_builtin
from agentlab.security import safeyaml
from agentlab.skills import SkillRegistry
from agentlab.skills.loader import load_skill_dir

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
NOT_OURS = {"node_modules", ".git", "venv", ".venv", "static", "__pycache__"}

# The pages that describe AgentLab. The bibliography only lists sources, and the decision records are history: neither
# makes claims about the code that could be compared with it.
CLAIMS = sorted(
    path
    for path in [ROOT / "README.md", ROOT / "CONTRIBUTING.md", *DOCS.rglob("*.md")]
    if path.name != "references.md" and "decisions" not in path.parts and not NOT_OURS & set(path.parts)
)


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def markdown_files() -> list[Path]:
    found = [*ROOT.glob("*.md"), *DOCS.rglob("*.md"), *(ROOT / ".ai").rglob("*.md"), *(ROOT / ".github").rglob("*.md")]
    return sorted(path for path in found if not NOT_OURS & set(path.parts))


# ================================================================================================================ reading
@dataclass(frozen=True)
class Block:
    """A fenced block of a document: its language, its text without the common indentation, and its first line."""

    language: str
    body: str
    line: int


def read(path: Path) -> tuple[list[str], list[Block]]:
    """A document as ``(prose, blocks)``. ``prose`` has one entry per line and is empty where the line is inside a fence,
    so line numbers still count from the top of the file."""
    prose: list[str] = []
    blocks: list[Block] = []
    fence = ""
    language = ""
    start = 0
    body: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not fence:
            opening = re.match(r"^(`{3,}|~{3,})\s*([\w+-]*)", stripped)
            if opening:
                fence, language, start, body = opening.group(1), opening.group(2).lower(), number, []
                prose.append("")
            else:
                prose.append(line)
            continue
        prose.append("")
        if len(stripped) >= len(fence) and set(stripped) == {fence[0]}:
            blocks.append(Block(language, textwrap.dedent("\n".join(body)) + "\n", start))
            fence = ""
        else:
            body.append(line)
    return prose, blocks


def prose_of(path: Path) -> str:
    return "\n".join(read(path)[0])


def blocks_of(*languages: str) -> Iterator[tuple[Path, Block]]:
    for path in CLAIMS:
        for block in read(path)[1]:
            if block.language in languages:
                yield path, block


def where(path: Path, block: Block) -> str:
    return f"{rel(path)}:{block.line}"


def text_of(paths: list[Path]) -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def table_rows(path: Path, under: str | None = None) -> Iterator[tuple[int, list[str]]]:
    """The rows of the tables of a page, as ``(line, cells)``: under the heading ``under`` when one is named."""
    inside = under is None
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if under is not None and line.startswith("#"):
            inside = line.lstrip("#").strip() == under
        if inside and line.startswith("|") and not set(line) <= set("|-: "):
            yield number, [cell.strip() for cell in line.strip().strip("|").split("|")]


# ===================================================================================================== links and anchors
def slug(heading: str) -> str:
    """The anchor GitHub gives a heading: lower case, punctuation dropped, spaces to hyphens."""
    text = heading.strip().lower()
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def anchors(path: Path) -> set[str]:
    seen: dict[str, int] = {}
    found: set[str] = set()
    for line in read(path)[0]:
        heading = re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", line)
        if heading:
            name = slug(heading.group(1))
            count = seen.get(name, 0)
            seen[name] = count + 1
            found.add(name if count == 0 else f"{name}-{count}")
    return found


def link_targets(path: Path) -> list[str]:
    text = re.sub(r"<!--.*?-->", "", prose_of(path), flags=re.S)
    text = re.sub(r"`[^`\n]*`", "", text)
    return [m.group(1) for m in re.finditer(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", text)]


@pytest.mark.parametrize("path", markdown_files(), ids=rel)
def test_every_relative_link_and_anchor_resolves(path: Path) -> None:
    problems = []
    for target in link_targets(path):
        if re.match(r"^(https?:|mailto:)", target):
            continue
        file_part, _, fragment = target.partition("#")
        destination = path if not file_part else (path.parent / file_part).resolve()
        if not destination.exists():
            problems.append(f"{target}: there is no such file")
        elif fragment and destination.suffix == ".md" and fragment.lower() not in anchors(destination):
            problems.append(f"{target}: {rel(destination)} has no heading with that anchor")
    assert not problems, f"{rel(path)}:\n  " + "\n  ".join(problems)


def test_the_readme_links_to_every_page_of_the_documentation() -> None:
    linked = {t.partition("#")[0] for t in link_targets(ROOT / "README.md")}
    pages = {rel(p) for p in DOCS.glob("*.md")} | {"docs/decisions/README.md"}
    assert pages <= linked, f"README.md does not link to {sorted(pages - linked)}"


# ======================================================================================================= configuration
def nested_model(annotation: Any, *, in_lists: bool) -> type[BaseModel] | None:
    """The model a field holds, or a list of them when asked, looking through ``X | None``."""
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        for part in typing.get_args(annotation):
            found = nested_model(part, in_lists=in_lists)
            if found:
                return found
        return None
    if in_lists and origin is list:
        return nested_model(typing.get_args(annotation)[0], in_lists=False)
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    return None


def config_paths(model: type[BaseModel], prefix: str = "") -> tuple[set[str], set[str]]:
    """``(every key path, the paths that only head other keys)`` of the configuration. The providers are a list of
    models and are documented key by key; no other list is."""
    everything: set[str] = set()
    headings: set[str] = set()
    for name, field in model.model_fields.items():
        path = f"{prefix}{name}"
        everything.add(path)
        inner = nested_model(field.annotation, in_lists=prefix == "")
        if inner is not None:
            headings.add(path)
            more, more_headings = config_paths(inner, f"{path}.")
            everything |= more
            headings |= more_headings
    return everything, headings


def configuration_rows() -> dict[str, tuple[int, str]]:
    """Every key docs/configuration.md lists in a table: its path (``security.sandbox.image``), the line it is on and
    its default as written."""
    rows: dict[str, tuple[int, str]] = {}
    section: str | None = None
    for number, line in enumerate((DOCS / "configuration.md").read_text(encoding="utf-8").splitlines(), 1):
        heading = re.match(r"^## (.*)$", line)
        if heading:
            title = heading.group(1).strip()
            section = "" if title == "Top level" else title.strip("`")
            continue
        if section is None or not line.startswith("|") or set(line) <= set("|-: "):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        names = re.findall(r"`([^`]+)`", cells[0])
        if not names or names[0] in {"Key", "Variable"}:
            continue
        parent = names[0].rpartition(".")[0]  # `sandbox.cpus` / `memory_mb`: the later names sit beside the first
        for index, name in enumerate(names):
            local = name if index == 0 or "." in name or not parent else f"{parent}.{name}"
            rows[f"{section}.{local}" if section else local] = (number, cells[1] if len(names) == 1 else "")
    return rows


def test_every_documented_configuration_key_exists_and_every_key_is_documented() -> None:
    real, headings = config_paths(AgentLabConfig)
    documented = configuration_rows()
    assert len(documented) > 60, "the tables of docs/configuration.md were not found"
    unknown = {key: row[0] for key, row in documented.items() if key not in real}
    assert not unknown, f"docs/configuration.md documents keys the configuration does not have (key: line): {unknown}"
    missing = sorted(real - headings - set(documented))
    assert not missing, f"keys of the configuration that docs/configuration.md does not document: {missing}"


def test_a_default_the_configuration_page_gives_as_a_number_or_a_switch_is_the_real_default() -> None:
    config = AgentLabConfig()
    wrong = []
    for path, (line, text) in configuration_rows().items():
        default = text.strip("`")
        if path.startswith("providers.") or not re.fullmatch(r"true|false|-?\d+(\.\d+)?", default):
            continue
        value: Any = config
        for part in path.split("."):
            value = getattr(value, part)
        expected: Any = {"true": True, "false": False}.get(default)
        if expected is None:
            expected = float(default) if "." in default else int(default)
        if value != expected or isinstance(value, bool) != isinstance(expected, bool):
            wrong.append(f"docs/configuration.md:{line}: {path} is documented as {default}, and is {value!r}")
    assert not wrong, "\n".join(wrong)


# ============================================================================================ what AgentLab ships with
def test_the_security_categories_are_the_ones_the_code_defines_in_both_pages_that_list_them() -> None:
    for page in ("security.md", "test-case-design.md"):
        rows = {}
        for _, cells in table_rows(DOCS / page):
            if len(cells) >= 3 and re.fullmatch(r"N\d+", cells[0]):
                rows[cells[0]] = (cells[1], set(re.findall(r"[a-z][a-z-]+-testing", cells[2])))
        assert set(rows) == {c.code for c in SECURITY_CATEGORIES}, f"{page}: the categories listed differ from the code"
        for category in SECURITY_CATEGORIES:
            name, skills = rows[category.code]
            assert name == category.name, f"{page}: {category.code} is {category.name!r} in the code, {name!r} here"
            assert skills == set(category.skills), f"{page}: {category.code} is tested by {sorted(category.skills)}"


def test_the_taxonomy_letters_are_the_ones_the_code_defines() -> None:
    rows = {
        cells[0]: cells[1] for _, cells in table_rows(DOCS / "test-case-design.md") if re.fullmatch(r"[A-Q]", cells[0])
    }
    assert rows == TAXONOMY


def test_the_built_in_skills_listed_are_the_ones_installed_and_every_count_given_is_right() -> None:
    registry = SkillRegistry.default([])
    installed = {name for name in registry.names() if registry.get(name).manifest.trust == "builtin"}
    listed = {
        match.group(1)
        for _, cells in table_rows(DOCS / "skills.md", under="The 30 built-in skills")
        if (match := re.fullmatch(r"`([a-z][a-z-]+)`", cells[0]))
    }
    assert listed == installed, f"listed only: {sorted(listed - installed)}; not listed: {sorted(installed - listed)}"
    for path in CLAIMS:
        for match in re.finditer(r"\b(\d+)(?= built-in skills\b)|\bAgentLab ships (\d+)\b", prose_of(path)):
            assert int(match.group(1) or match.group(2)) == len(installed), f"{rel(path)}: {match.group(0)!r}"


def test_the_security_category_count_is_the_number_of_categories() -> None:
    for path in CLAIMS:
        for match in re.finditer(r"\b(\d+)(?= security categories\b)", prose_of(path)):
            assert int(match.group(1)) == len(SECURITY_CATEGORIES), f"{rel(path)}: {match.group(0)!r}"


def test_the_assertions_listed_are_the_ones_registered_and_every_count_given_is_right() -> None:
    text = (DOCS / "evaluation.md").read_text(encoding="utf-8")
    section = text.split("## Deterministic assertions", 1)[1].split("\n## ", 1)[0]
    listed = set(re.findall(r"^\| `([a-z_]+)` \|", section, re.M))
    registered = {name for name in ASSERTIONS.names() if is_builtin(ASSERTIONS.get(name))}
    assert listed == registered, (
        f"listed only: {sorted(listed - registered)}; not listed: {sorted(registered - listed)}"
    )
    for path in CLAIMS:
        for match in re.finditer(
            r"\b(\d+)(?= (?:built-in kinds|checks|assertion kinds|built in|below)\b)", prose_of(path)
        ):
            assert int(match.group(1)) == len(registered), f"{rel(path)}: {match.group(0)!r}"


def test_the_plug_in_guide_has_a_row_for_every_registry_there_is() -> None:
    guide = (DOCS / "plugins.md").read_text(encoding="utf-8")
    for kind in [*all_registries(), "skills"]:
        assert f"`agentlab.{kind}`" in guide, f"docs/plugins.md does not list the entry-point group agentlab.{kind}"


def test_the_complete_skill_example_is_a_valid_skill(tmp_path: Path) -> None:
    example = next(
        block
        for path, block in blocks_of("yaml")
        if path.name == "skills.md"
        and safeyaml.load(block.body).get("name") == "refund-policy-checks"
        and "templates" in safeyaml.load(block.body)
    )
    (tmp_path / "skill.yaml").write_text(example.body, encoding="utf-8")
    skill = load_skill_dir(tmp_path, require_doc=False)
    assert skill.problems == []
    assert skill.name == "refund-policy-checks"


# ============================================================================================== command-line interface
def commands() -> dict[tuple[str, ...], Any]:
    """Every command of the command line, by its words (``("skills", "list")``), groups included. Hidden ones are
    left out: they are not meant to be documented."""
    found: dict[tuple[str, ...], Any] = {}

    def walk(command: Any, path: tuple[str, ...]) -> None:
        for name, child in sorted(getattr(command, "commands", {}).items()):
            if not getattr(child, "hidden", False):
                found[(*path, name)] = child
                walk(child, (*path, name))

    walk(typer.main.get_command(cli_app), ())
    return found


def options_of(command: Any) -> dict[str, Any]:
    found: dict[str, Any] = {"--help": None}
    for param in getattr(command, "params", []):
        for name in [*getattr(param, "opts", []), *getattr(param, "secondary_opts", [])]:
            if name.startswith("-"):
                found[name] = param
    return found


def words(line: str) -> list[str]:
    """The words of a shell line up to its first pipe, redirection, ``;``, ``&`` or comment."""
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    found: list[str] = []
    try:
        for token in lexer:
            if set(token) <= set("();<>|&"):
                break
            found.append(token)
    except ValueError:
        pass  # an unfinished quote: what came before it is still a command
    return found


def command_lines() -> Iterator[tuple[str, str, list[str]]]:
    """Every shell line in the documents that runs ``agentlab``: ``(where, the line, its words)``."""
    for path, block in blocks_of("bash", "sh", "shell", "console", ""):
        joined = re.sub(r"\\\n\s*", " ", block.body)
        for offset, raw in enumerate(joined.splitlines()):
            tokens = words(re.sub(r"^\$\s*", "", raw.strip()))
            while tokens and re.match(r"^[A-Z_][A-Z0-9_]*=", tokens[0]):
                tokens = tokens[1:]  # NAME=value agentlab ...
            if tokens and tokens[0] == "agentlab":
                yield where(path, block), raw.strip(), tokens


def problems_in(tokens: list[str]) -> list[str]:
    """What is wrong with a command line that starts with ``agentlab``: a command or an option that does not exist."""
    command: Any = typer.main.get_command(cli_app)
    allowed = options_of(command)
    path = ["agentlab"]
    index = 1
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if token.startswith("-") and token != "-":
            name = token.split("=", 1)[0]
            if name not in allowed:
                return [f"{name} is not an option of `{' '.join(path)}`"]
            param = allowed[name]
            if param is not None and not getattr(param, "is_flag", False) and not getattr(param, "count", False):
                index += 0 if "=" in token else 1  # the next word is this option's value
            continue
        children = getattr(command, "commands", None)
        if children is None:
            continue  # an argument of a command
        if token not in children:
            return [f"{token!r} is not a command of `{' '.join(path)}`"]
        command = children[token]
        allowed |= options_of(command)
        path.append(token)
    return []


def test_every_command_of_the_command_line_is_documented_as_it_is_typed() -> None:
    text = text_of(CLAIMS)
    typed = [" ".join(("agentlab", *path)) for path in commands()]
    assert not [command for command in typed if command not in text], "no document mentions one of the commands"


def test_every_agentlab_line_in_a_document_names_real_commands_and_options() -> None:
    lines = list(command_lines())
    assert len(lines) > 30, "no examples were found; did the way commands are written in the documents change?"
    bad = [f"{place}: `{line}`: {problem}" for place, line, tokens in lines for problem in problems_in(tokens)]
    assert not bad, "\n".join(bad)


def test_the_exit_codes_documented_are_the_ones_the_command_line_returns() -> None:
    section = (DOCS / "testing-agents.md").read_text(encoding="utf-8").split("## Exit codes", 1)[1].split("\n## ", 1)[0]
    documented = {int(code) for code in re.findall(r"^\| `(\d)` \|", section, re.M)}
    real = {
        cli_common.EXIT_OK,
        cli_common.EXIT_FINDINGS,
        cli_common.EXIT_INPUT,
        cli_common.EXIT_INCOMPLETE,
        cli_common.EXIT_NOT_TESTED,
    }
    assert documented == real


# ============================================================================================================== REST API
def test_every_route_a_document_names_exists_in_the_openapi_document() -> None:
    schema = create_app(config=AgentLabConfig(), serve_ui=False).openapi()
    routes = {
        re.sub(r"\{[^}]*\}", "{}", path): {method.upper() for method in methods}
        for path, methods in schema["paths"].items()
    }
    not_described = {
        "/docs",
        "/redoc",
        "/openapi.json",
        "/view/{}",
    }  # the reference itself, and the signed report links
    bad = []
    named = 0
    for path in CLAIMS:
        for match in re.finditer(
            r"\b(GET|POST|PUT|PATCH|DELETE) (/[A-Za-z0-9_{}/.\-]*)", path.read_text(encoding="utf-8")
        ):
            route = re.sub(r"\{[^}]*\}", "{}", match.group(2).rstrip(".,"))
            named += 1
            if route not in not_described and match.group(1) not in routes.get(route, set()):
                bad.append(f"{rel(path)}: {match.group(1)} {match.group(2)}")
    assert named > 10, "no routes were found in the pages"
    assert not bad, "\n".join(bad)


def test_the_openapi_file_the_web_interface_is_generated_from_is_current() -> None:
    have = json.loads((ROOT / "web" / "openapi.json").read_text(encoding="utf-8"))
    now = json.loads(json.dumps(create_app(config=AgentLabConfig(), serve_ui=False).openapi()))
    assert have == now, (
        "web/openapi.json is out of date: python scripts/export_openapi.py && (cd web && npm run gen:api)"
    )


# ====================================================================================================== environment
USES = re.compile(
    r"""(?:environ(?:\.get|\.pop)?|getenv)\s*[(\[]\s*["'](AGENTLAB_[A-Z0-9_]+)["']"""  # read: os.environ.get("NAME")
    r"""|envvar\s*=\s*["'](AGENTLAB_[A-Z0-9_]+)["']"""  # read: typer.Option(envvar="NAME")
    r"""|\benv\s*=\s*\{\s*["'](AGENTLAB_[A-Z0-9_]+)["']"""  # set for a command AgentLab runs: env={"NAME": ...}
)


def variables_the_code_uses() -> set[str]:
    """The ``AGENTLAB_*`` variables the code reads, or sets for the commands it starts. A canary is a value, not a
    variable, and is left out."""
    found: set[str] = set()
    for path in (ROOT / "src" / "agentlab").rglob("*.py"):
        for match in USES.finditer(path.read_text(encoding="utf-8")):
            found |= {name for name in match.groups() if name and "CANARY" not in name}
    return found


def test_every_environment_variable_the_code_uses_is_documented() -> None:
    used = variables_the_code_uses()
    assert used >= {"AGENTLAB_CONFIG", "AGENTLAB_MASTER_KEY", "AGENTLAB_SESSION"}, "the pattern no longer finds them"
    documented = set(re.findall(r"AGENTLAB_[A-Z0-9_]+", text_of(CLAIMS)))
    assert not sorted(used - documented), f"used by the code and named in no document: {sorted(used - documented)}"


def test_every_environment_variable_a_document_names_is_used_somewhere() -> None:
    sources = [
        *(ROOT / "src" / "agentlab").rglob("*.py"),
        *(ROOT / "tests").rglob("*.py"),
        *(ROOT / "docker").glob("*"),
        *(ROOT / "scripts").rglob("*"),
        ROOT / "web" / "vite.config.ts",
    ]
    code = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in sources if p.is_file())
    named = {name for name in re.findall(r"AGENTLAB_[A-Z0-9_]+", text_of(CLAIMS)) if "CANARY" not in name}
    unused = sorted(name for name in named if name not in code)
    assert not unused, f"named in a document and nowhere in the code: {unused}"


# ======================================================================================================== the repository
PATH_IN_CODE = re.compile(r"`((?:src|tests|docs|docker|scripts|web|\.ai|\.github)/[A-Za-z0-9_./\-]*[A-Za-z0-9_])`")


def test_every_repository_path_a_document_names_exists() -> None:
    bad = []
    for path in CLAIMS:
        for number, line in enumerate(prose_of(path).splitlines(), 1):
            for match in PATH_IN_CODE.finditer(line):
                named = match.group(1)
                if not any(ch in named for ch in "*<>{}") and not (ROOT / named).exists():
                    bad.append(f"{rel(path)}:{number}: {named}")
    assert not bad, "\n".join(bad)


def test_every_decision_record_is_in_the_index_with_its_status() -> None:
    index = (DOCS / "decisions" / "README.md").read_text(encoding="utf-8")
    entries = {
        m.group(1): (m.group(2), m.group(3)) for m in re.finditer(r"^\[(\d{4})\]\(([^)]+)\) — .* — (\w+)$", index, re.M)
    }
    records = sorted((DOCS / "decisions").glob("[0-9][0-9][0-9][0-9]-*.md"))
    assert {record.name for record in records} == {name for name, _ in entries.values()}, (
        "the index and the folder differ"
    )
    for record in records:
        status = re.search(r"^- Status: (\w+)", record.read_text(encoding="utf-8"), re.M)
        assert status, f"{rel(record)} has no `- Status:` line"
        assert entries[record.name[:4]][1] == status.group(1), f"{rel(record)}: the index shows another status"


# ================================================================================================ examples in the pages
def test_python_examples_are_valid_and_import_what_exists() -> None:
    bad = []
    seen = 0
    for path, block in blocks_of("python", "py"):
        seen += 1
        try:
            tree = ast.parse(block.body)
        except SyntaxError as error:
            bad.append(f"{where(path, block)}: {error}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                modules = [(node.module, [alias.name for alias in node.names])]
            elif isinstance(node, ast.Import):
                modules = [(alias.name, []) for alias in node.names]
            else:
                continue
            for module, names in modules:
                if module.split(".")[0] != "agentlab":
                    continue
                try:
                    imported = importlib.import_module(module)
                except ImportError as error:
                    bad.append(f"{where(path, block)}: cannot import {module} ({error})")
                    continue
                bad += [
                    f"{where(path, block)}: {module} has no {name}" for name in names if not hasattr(imported, name)
                ]
    assert seen > 5
    assert not bad, "\n".join(bad)


def check_yaml(data: Any) -> str | None:
    """What a YAML example should be, judged by its keys: ``None`` when it is fine, else why not. An example that fits
    no shape here is only required to be YAML."""
    if not isinstance(data, dict) or not data:
        return None
    keys = set(data)
    if keys <= set(AgentLabConfig.model_fields):
        AgentLabConfig.model_validate(data)
    elif keys <= set(TargetSpec.model_fields):
        TargetSpec.model_validate({"name": "example", **data})
    elif keys == {"assertions"}:
        for item in data["assertions"]:
            spec = AssertionSpec.model_validate(item)
            if spec.type not in ASSERTIONS.names():
                return f"there is no assertion called {spec.type!r}"
    elif {"weights", "security_caps", "grades"} <= keys:
        ScoringProfile.model_validate(data).validate_categories()
    return None


def test_yaml_examples_parse_and_the_ones_with_a_known_shape_are_valid() -> None:
    bad = []
    seen = 0
    for path, block in blocks_of("yaml", "yml"):
        seen += 1
        try:
            problem = check_yaml(safeyaml.load(block.body))
        except Exception as error:  # noqa: BLE001 - whatever is wrong with the example is the message
            bad.append(f"{where(path, block)}: {type(error).__name__}: {str(error).splitlines()[0][:200]}")
            continue
        if problem:
            bad.append(f"{where(path, block)}: {problem}")
    assert seen > 10
    assert not bad, "\n".join(bad)


def test_the_test_cases_written_in_the_pages_load(tmp_path: Path) -> None:
    bad = []
    loaded = 0
    for index, (path, block) in enumerate(blocks_of("yaml", "yml")):
        data = safeyaml.load(block.body)
        tests = data.get("tests") if isinstance(data, dict) else None
        if not isinstance(tests, list) or not all(isinstance(t, dict) and t.get("name") for t in tests):
            continue  # a skill's templates, or part of a test, are not a test file
        file = tmp_path / f"example-{index}.yaml"
        file.write_text(block.body, encoding="utf-8")
        found, problems = load_user_tests([file])
        loaded += len(found)
        bad += [f"{where(path, block)}: {problem}" for problem in problems]
    assert loaded > 0, "no test file was found in the pages"
    assert not bad, "\n".join(bad)


# =============================================================================================================== stubs
UNFINISHED = re.compile(
    r"\b(?:FIXME|XXX|lorem ipsum|coming soon|to be written|Status: TBD)\b|\bTODO\b(?! \(human review\))", re.IGNORECASE
)


def test_no_page_is_a_stub() -> None:
    bad = []
    for path in CLAIMS:
        for number, line in enumerate(prose_of(path).splitlines(), 1):
            if UNFINISHED.search(line):
                bad.append(f"{rel(path)}:{number}: {line.strip()[:100]}")
    assert not bad, "\n".join(bad)
