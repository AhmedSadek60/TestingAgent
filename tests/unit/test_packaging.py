"""What the code imports is what ``pyproject.toml`` installs.

A package that is imported but declared nowhere works on the machine of whoever wrote the code and fails on a clean
install (the Anthropic provider needed the ``anthropic`` package, which no extra listed). This reads every import in
``src/`` and checks that the package it comes from is a dependency or an extra.
"""

from __future__ import annotations

import ast
import importlib.metadata
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "agentlab"


def normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_name(requirement: str) -> str:
    return normalise(re.split(r"[\s<>=!~\[;(]", requirement.strip(), maxsplit=1)[0])


def declared() -> set[str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    requirements = list(project["dependencies"])
    for extra in project["optional-dependencies"].values():
        requirements.extend(extra)
    return {requirement_name(r) for r in requirements}


def imported() -> dict[str, Path]:
    """Every top-level module imported anywhere under ``src/agentlab``, with a file that imports it."""
    found: dict[str, Path] = {}
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                found.setdefault(name.split(".")[0], path)
    return found


def test_every_package_the_code_imports_is_a_dependency_or_an_extra() -> None:
    owners = importlib.metadata.packages_distributions()
    known = declared()
    missing = {}
    for module, path in imported().items():
        if module == "agentlab" or module in sys.stdlib_module_names:
            continue
        distributions = {normalise(d) for d in owners.get(module, [module])}
        if not distributions & known:
            missing[module] = f"{path.relative_to(ROOT)} (installed from: {sorted(distributions)})"
    assert not missing, f"imported but not in pyproject.toml: {missing}"


def test_the_all_extra_installs_every_other_extra_but_the_development_tools() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    extras = project["optional-dependencies"]
    inside_all = {requirement_name(r) for r in extras["all"]}
    assert inside_all == {"agentlab"}, "`all` should be written as agentlab[...] of the other extras"
    listed = re.search(r"\[(.*)\]", extras["all"][0])
    assert listed is not None
    assert set(listed.group(1).split(",")) == set(extras) - {"all", "dev"}
