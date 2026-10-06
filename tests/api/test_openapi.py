"""The OpenAPI document is the API's reference (spec section 41: every request and response documented), so it is held to a
standard: nothing a caller can send or receive is left without an explanation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agentlab.api.openapi_docs import unknown_entries
from tests.support.api import running_api

# The endpoints the specification names (section 41).
REQUIRED = [
    ("post", "/projects"),
    ("post", "/targets"),
    ("post", "/credentials"),
    ("post", "/documents"),
    ("post", "/discover"),
    ("post", "/test-plans"),
    ("post", "/test-runs"),
    ("post", "/test-runs/{run_id}/cancel"),
    ("get", "/test-runs/{run_id}"),
    ("get", "/test-runs/{run_id}/traces"),
    ("get", "/test-runs/{run_id}/results"),
    ("get", "/reports/{report_id}"),
    ("post", "/reports/{report_id}/export"),
    ("get", "/providers"),
    ("get", "/models"),
    ("get", "/skills"),
]


@pytest.fixture
async def schema(tmp_path: Path) -> dict[str, Any]:
    async with running_api(tmp_path) as api:
        doc: dict[str, Any] = api.app.openapi()
        return doc


def operations(doc: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (method, path, op)
        for path, methods in doc["paths"].items()
        for method, op in methods.items()
        if method in {"get", "post", "put", "patch", "delete"}
    ]


def test_every_endpoint_the_specification_names_exists(schema: dict[str, Any]) -> None:
    have = {(m, p) for m, p, _ in operations(schema)}
    assert [r for r in REQUIRED if r not in have] == []


def test_every_operation_has_a_name_an_explanation_and_a_group(schema: dict[str, Any]) -> None:
    declared = {t["name"] for t in schema["tags"]}
    ids: set[str] = set()
    for method, path, op in operations(schema):
        where = f"{method.upper()} {path}"
        assert op.get("summary") and len(op["summary"]) <= 60, where
        assert op.get("description") and len(op["description"]) >= 20, f"{where} needs a real description"
        assert op.get("tags") and set(op["tags"]) <= declared, where
        assert op["operationId"] not in ids, f"{where}: duplicate operationId"
        ids.add(op["operationId"])
        success = [c for c in op["responses"] if c.startswith("2")]
        assert success, f"{where} documents no success response"
        for code, resp in op["responses"].items():
            assert resp.get("description"), f"{where} {code} has no description"
    for tag in schema["tags"]:
        assert tag["description"], tag


def test_every_parameter_says_what_it_is_for(schema: dict[str, Any]) -> None:
    gaps = [
        f"{method.upper()} {path}: {p['name']}"
        for method, path, op in operations(schema)
        for p in op.get("parameters", [])
        if not p.get("description")
    ]
    assert gaps == []


def test_every_field_of_every_request_and_response_is_explained(schema: dict[str, Any]) -> None:
    gaps = [
        f"{name}.{prop}"
        for name, model in schema["components"]["schemas"].items()
        for prop, body in (model.get("properties") or {}).items()
        if not body.get("description")
    ]
    assert gaps == [], f"{len(gaps)} undocumented fields, for example {gaps[:8]}"
    unexplained = [n for n, m in schema["components"]["schemas"].items() if not m.get("description")]
    assert unexplained == [], f"models with no description: {unexplained}"


def test_the_glossary_does_not_describe_things_that_no_longer_exist(schema: dict[str, Any]) -> None:
    assert unknown_entries(schema) == []


def test_every_protected_operation_documents_how_it_can_fail(schema: dict[str, Any]) -> None:
    for method, path, op in operations(schema):
        if path == "/health":
            continue
        codes = set(op["responses"])
        assert {"401", "403", "404", "422", "429"} <= codes, f"{method.upper()} {path} documents only {sorted(codes)}"
        for code in ("401", "404", "422"):
            content = op["responses"][code]["content"]["application/json"]["schema"]
            assert content["$ref"].endswith("/ErrorResponse"), (method, path, code)


def test_the_document_is_self_consistent(schema: dict[str, Any]) -> None:
    assert schema["openapi"].startswith("3.1")
    assert schema["info"]["title"] == "AgentLab API" and schema["info"]["version"]
    refs: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "$ref":
                    refs.append(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)
    assert refs
    for ref in refs:
        assert ref.startswith("#/components/schemas/"), ref
        assert ref.rsplit("/", 1)[-1] in schema["components"]["schemas"], f"dangling reference {ref}"
    unused = set(schema["components"]["schemas"]) - {r.rsplit("/", 1)[-1] for r in refs}
    assert not unused, f"schemas nothing refers to: {sorted(unused)}"


async def test_the_reference_pages_are_served(tmp_path: Path) -> None:
    async with running_api(tmp_path) as api:
        assert (await api.client.get("/openapi.json")).json()["info"]["title"] == "AgentLab API"
        docs = await api.client.get("/docs")
        assert docs.status_code == 200 and "swagger" in docs.text.lower()
        assert (await api.client.get("/redoc")).status_code == 200


def test_the_schema_the_web_interface_is_built_from_is_the_current_one() -> None:
    """The interface's TypeScript types are generated from ``web/openapi.json``. When the API changes, the committed copy has
    to be regenerated, or the interface would be built against an API that no longer exists."""
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "scripts" / "export_openapi.py"
    spec = importlib.util.spec_from_file_location("export_openapi", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    have = module.TARGET.read_text(encoding="utf-8")
    assert have == module.current(), (
        "web/openapi.json is out of date: run `python scripts/export_openapi.py && (cd web && npm run gen:api)`"
    )


def test_the_choices_a_request_can_name_are_listed_in_the_schema(schema: dict[str, Any]) -> None:
    """The web client's types are generated from these lists, so a choice the server does not know cannot be offered."""
    from agentlab.design import SUITES
    from agentlab.skills.context import INTENSITIES

    for name in ("JobOptions", "TestPlan"):
        props = schema["components"]["schemas"][name]["properties"]
        assert props["intensity"]["enum"] == list(INTENSITIES), name
        assert set(props["suite"]["enum"]) == set(SUITES), name
