"""Testing an agent given only its OpenAPI document.

``agentlab test --openapi URL`` used to build a target whose address *was the document*: every test POSTed a chat message to
``/openapi.json``. The endpoint is now found in the document, and only when the document says nothing the owner did not
authorise: tests are sent to the host the document was read from, never to a server the document merely names.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from agentlab.adapters.base import AdapterContext
from agentlab.adapters.http import AgentApiAdapter
from agentlab.adapters.openapi import (
    analyze_openapi,
    parse_openapi_text,
    resolve_api,
)
from agentlab.core.config import AgentLabConfig
from agentlab.core.errors import TargetError
from agentlab.core.models import ApiConfig, TargetSpec
from agentlab.core.models.target import DEFAULT_REQUEST_TEMPLATE
from agentlab.security import safeyaml
from agentlab.security.egress import EgressPolicy

DOC_URL = "http://agent.test:8000/openapi.json"


def chat_doc(**over: Any) -> dict[str, Any]:
    """A small, typical document: a health check and a chat endpoint with its own field names."""
    doc: dict[str, Any] = {
        "openapi": "3.1.0",
        "info": {"title": "Acme Assistant", "version": "2"},
        "paths": {
            "/health": {"get": {"summary": "Health", "responses": {"200": {"description": "ok"}}}},
            "/v1/ask": {
                "post": {
                    "summary": "Ask",
                    "requestBody": {
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/AskRequest"}},
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/AskResponse"}}},
                        }
                    },
                }
            },
        },
        "components": {
            "schemas": {
                "AskRequest": {
                    "type": "object",
                    "required": ["question"],
                    "properties": {"question": {"type": "string"}, "conversation_id": {"type": "string"}},
                },
                "AskResponse": {
                    "type": "object",
                    "properties": {"answer": {"type": "string"}, "usage": {"type": "object"}},
                },
            }
        },
    }
    doc.update(over)
    return doc


def target(**api: Any) -> TargetSpec:
    return TargetSpec(name="t", api=ApiConfig(openapi_url=DOC_URL, **api))


def resolved(doc: dict[str, Any], spec: TargetSpec | None = None, url: str | None = DOC_URL):  # type: ignore[no-untyped-def]
    return resolve_api(spec or target(), analyze_openapi(doc), document_url=url)


# ------------------------------------------------------------------------------------------------- the model
def test_an_api_needs_an_address_or_a_document_to_find_one_in() -> None:
    with pytest.raises(ValidationError, match="an api needs an address"):
        ApiConfig()
    assert ApiConfig(url="http://a/chat").openapi_url is None
    assert ApiConfig(openapi_url="http://a/openapi.json").url == ""


async def test_an_api_without_an_address_cannot_be_opened_and_says_what_to_set() -> None:
    spec = TargetSpec(name="t", api=ApiConfig(openapi_url=DOC_URL))
    adapter = AgentApiAdapter(spec, AdapterContext(config=AgentLabConfig(), egress=EgressPolicy()))  # type: ignore[call-arg]
    with pytest.raises(TargetError, match="--api-url"):
        await adapter.open()


# -------------------------------------------------------------------------------------------- reading a document
def test_the_endpoint_its_fields_and_the_answer_are_found_through_refs() -> None:
    analysis = analyze_openapi(chat_doc())
    ask = analysis.best_chat_endpoint()
    assert ask is not None and (ask.method, ask.path) == ("POST", "/v1/ask")
    assert (ask.input_field, ask.session_field) == ("question", "conversation_id")
    assert ask.response_fields == ["answer", "usage"] and not ask.streaming
    assert [e.path for e in analysis.endpoints] == ["/health", "/v1/ask"]
    assert analysis.title == "Acme Assistant"


def test_properties_inherited_through_allof_and_request_bodies_behind_a_ref_are_found() -> None:
    doc = chat_doc()
    doc["paths"]["/v1/ask"]["post"]["requestBody"] = {"$ref": "#/components/requestBodies/Ask"}
    doc["components"]["requestBodies"] = {
        "Ask": {
            "content": {
                "application/json; charset=utf-8": {"schema": {"allOf": [{"$ref": "#/components/schemas/Base"}]}}
            }
        }
    }
    doc["components"]["schemas"]["Base"] = {
        "properties": {"prompt": {"type": "string"}, "thread_id": {"type": "string"}}
    }
    ask = analyze_openapi(doc).best_chat_endpoint()
    assert ask is not None and (ask.input_field, ask.session_field) == ("prompt", "thread_id")


def test_a_message_field_that_is_not_text_is_not_a_message_field() -> None:
    doc = chat_doc()
    doc["components"]["schemas"]["AskRequest"]["properties"] = {"question": {"type": "array"}}
    assert analyze_openapi(doc).best_chat_endpoint() is None


def test_swagger_two_bodies_responses_and_servers_are_read() -> None:
    doc = {
        "swagger": "2.0",
        "host": "api.test",
        "basePath": "/v2",
        "schemes": ["https"],
        "produces": ["text/event-stream"],
        "paths": {
            "/chat": {
                "post": {
                    "parameters": [
                        {"in": "body", "name": "body", "schema": {"properties": {"message": {"type": "string"}}}},
                    ],
                    "responses": {
                        "200": {"description": "stream", "schema": {"properties": {"reply": {"type": "string"}}}}
                    },
                }
            }
        },
        "securityDefinitions": {"key": {"type": "apiKey", "in": "header"}},
    }
    analysis = analyze_openapi(doc)
    chat = analysis.best_chat_endpoint()
    assert chat is not None and chat.input_field == "message" and chat.streaming and not chat.json_response
    assert analysis.servers == ["https://api.test/v2"] and analysis.security_schemes == {"key": "apiKey:header"}


def test_required_fields_the_document_gives_a_value_for_are_sent_and_the_others_are_reported() -> None:
    doc = chat_doc()
    doc["components"]["schemas"]["AskRequest"].update(
        required=["question", "mode", "lang", "user_id"],
        properties={
            "question": {"type": "string"},
            "mode": {"type": "string", "enum": ["fast", "slow"]},
            "lang": {"type": "string", "default": "en"},
            "user_id": {"type": "string"},
        },
    )
    ask = analyze_openapi(doc).endpoints[1]
    assert ask.defaults == {"mode": "fast", "lang": "en"} and ask.unfillable == ["user_id"]


@pytest.mark.parametrize(
    "document",
    [
        None,
        [],
        "text",
        42,
        {"paths": []},
        {"paths": {"/a": []}},
        {"paths": {"/a": {"post": "x"}}},
        {"paths": {"/a": {"post": {"requestBody": [], "responses": []}}}},
        {"paths": {"/a": {"post": {"requestBody": {"content": []}, "responses": {"200": "x"}}}}},
        {"paths": {"/a": {"$ref": "#/paths/~1a"}}},
        {"info": [], "servers": "x", "components": [], "security": "x"},
        {"servers": [1, {"url": 3}, {"url": ""}, "http://a"], "components": {"securitySchemes": {"k": []}}},
        {"swagger": "2.0", "host": 3, "basePath": 4, "schemes": "https"},
        {
            "paths": {
                "/c": {"post": {"requestBody": {"content": {"application/json": {"schema": {"$ref": "#/loop"}}}}}}
            },
            "loop": {"$ref": "#/loop"},
        },
    ],
)
def test_a_document_of_any_shape_is_analysed_without_raising(document: Any) -> None:
    analysis = analyze_openapi(document)
    assert analysis.best_chat_endpoint() is None


def test_a_document_with_thousands_of_operations_is_cut_and_says_so() -> None:
    doc = {"paths": {f"/p{i}": {"get": {}, "post": {}} for i in range(1500)}}
    analysis = analyze_openapi(doc)
    assert analysis.truncated and len(analysis.endpoints) == 2000


def test_candidates_are_ordered_by_what_can_be_called_then_by_score_never_by_document_order() -> None:
    def post(path: str, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        schema = {"required": ["message"], "properties": {"message": {"type": "string"}, **(extra or {})}}
        return {"post": {"requestBody": {"content": {"application/json": {"schema": schema}}}}}

    paths = {
        "/agents/{id}/chat": post("/agents/{id}/chat"),  # best name, but needs a value for {id}
        "/needs-user": post("/needs-user", extra={"user": {"type": "string"}}),
        "/zeta": post("/zeta"),
        "/chat": post("/chat"),
        "/alpha": post("/alpha"),
    }
    paths["/needs-user"]["post"]["requestBody"]["content"]["application/json"]["schema"]["required"] = [
        "message",
        "user",
    ]
    forward = [e.path for e in analyze_openapi({"paths": paths}).chat_candidates()]
    backward = [e.path for e in analyze_openapi({"paths": dict(reversed(paths.items()))}).chat_candidates()]
    # equal scores: the shorter path first, then alphabetical; whatever cannot be called as it is comes last
    assert forward == backward == ["/chat", "/zeta", "/alpha", "/agents/{id}/chat", "/needs-user"]


@pytest.mark.parametrize(
    ("template", "requested", "expected"),
    [
        ("/chat", "/chat", True),
        ("/chat", "/v1/chat", True),
        ("/chat", "/v1/chat/", True),
        ("/chat", "/subchat", False),
        ("/chat", "/chat/extra", False),
        ("/agents/{id}/chat", "/api/agents/7/chat", True),
        ("/agents/{id}/chat", "/agents//chat", False),
        ("/", "/anything", False),
    ],
)
def test_a_request_path_matches_an_operation_on_whole_segments(template: str, requested: str, expected: bool) -> None:
    doc = {
        "paths": {
            template: {
                "post": {"requestBody": {"content": {"application/json": {"schema": {"properties": {"message": {}}}}}}}
            }
        }
    }
    assert (analyze_openapi(doc).endpoint_at(requested, "post") is not None) is expected


# --------------------------------------------------------------------- no address: the endpoint comes from the document
def test_the_chat_endpoint_is_chosen_and_its_request_and_answer_are_taken_from_the_document() -> None:
    res = resolved(chat_doc())
    api = res.spec.api
    assert api is not None and api.url == "http://agent.test:8000/v1/ask" and api.method == "POST"
    assert api.request_template == {"question": "{{input}}", "conversation_id": "{{session_id}}"}
    assert api.response.output == "$.answer" and api.protocol == "rest"
    assert api.openapi_url == DOC_URL, "the document stays recorded as where this came from"
    assert any("tests go to POST http://agent.test:8000/v1/ask" in n for n in res.notes), res.notes
    assert not res.warnings


def test_the_owners_input_object_is_not_modified_and_nothing_is_changed_when_nothing_applies() -> None:
    spec = target()
    res = resolve_api(spec, analyze_openapi(chat_doc()), document_url=DOC_URL)
    assert res.spec is not spec and res.spec.api is not None and res.spec.api.url
    assert spec.api is not None and spec.api.url == "", "resolution returns a copy"
    plain = TargetSpec(name="t", description="no api at all")
    assert resolve_api(plain, analyze_openapi(chat_doc()), document_url=DOC_URL).spec is plain


@pytest.mark.parametrize(
    ("servers", "expected"),
    [
        (None, "http://agent.test:8000/v1/ask"),
        ([{"url": "/api"}], "http://agent.test:8000/api/v1/ask"),
        ([{"url": "http://agent.test:8000/base/"}], "http://agent.test:8000/base/v1/ask"),
        ([{"url": "http://AGENT.test:8000"}], "http://AGENT.test:8000/v1/ask"),
        (
            [{"url": "{scheme}://agent.test:8000", "variables": {"scheme": {"default": "http"}}}],
            "http://agent.test:8000/v1/ask",
        ),
        ([{"url": "https://elsewhere.test"}, {"url": "/real"}], "http://agent.test:8000/real/v1/ask"),
    ],
)
def test_a_server_on_the_documents_own_host_is_used(servers: list[dict[str, Any]] | None, expected: str) -> None:
    doc = chat_doc(**({"servers": servers} if servers else {}))
    api = resolved(doc).spec.api
    assert api is not None and api.url == expected


@pytest.mark.parametrize(
    "server",
    [
        "https://api.example.com",
        "http://agent.test:9000",  # the same host on another port is another service
        "https://agent.test:8000",  # and so is another scheme
        "//other.test/v1",
        "http://169.254.169.254/latest",
        "file:///etc/passwd",
    ],
)
def test_a_server_the_document_merely_names_is_never_sent_tests_and_the_owner_is_told_how_to_confirm_it(
    server: str,
) -> None:
    spec = target()
    res = resolve_api(spec, analyze_openapi(chat_doc(servers=[{"url": server}])), document_url=DOC_URL)
    assert res.spec is spec, "nothing was resolved"
    (warning,) = res.warnings
    assert "tests are not sent to a host the target does not name" in warning and "--api-url" in warning


def test_a_servers_user_name_and_password_are_not_repeated_in_messages() -> None:
    doc = chat_doc(servers=[{"url": "https://admin:hunter2-password@evil.example/v1?token=abc123"}])
    (warning,) = resolved(doc).warnings
    assert "hunter2" not in warning and "abc123" not in warning and "admin" not in warning
    assert "https://evil.example/v1/v1/ask" in warning  # the server's own path, then the operation's


def test_other_candidates_are_named_so_the_choice_can_be_overridden() -> None:
    doc = chat_doc()
    doc["paths"]["/v1/chat"] = doc["paths"]["/v1/ask"]
    res = resolved(doc)
    assert any("2 endpoints look like chat" in n and "Set api.url" in n for n in res.notes), res.notes


@pytest.mark.parametrize(
    ("doc", "fragment"),
    [
        ({"openapi": "3.0.0", "paths": {"/health": {"get": {}}}}, "none of the 1 operation(s)"),
        (
            {
                "openapi": "3.0.0",
                "paths": {
                    "/a/{id}/chat": {
                        "post": {
                            "requestBody": {
                                "content": {"application/json": {"schema": {"properties": {"message": {}}}}}
                            }
                        }
                    }
                },
            },
            "path parameter",
        ),
        (
            {
                "openapi": "3.0.0",
                "paths": {
                    "/chat": {
                        "post": {
                            "requestBody": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "required": ["message", "tenant"],
                                            "properties": {"message": {}, "tenant": {}},
                                        }
                                    }
                                }
                            }
                        }
                    }
                },
            },
            "requires 'tenant'",
        ),
    ],
)
def test_when_no_endpoint_can_be_chosen_the_target_is_left_alone_and_the_owner_is_told_what_to_add(
    doc: dict[str, Any], fragment: str
) -> None:
    spec = target()
    res = resolve_api(spec, analyze_openapi(doc), document_url=DOC_URL)
    assert res.spec is spec and len(res.warnings) == 1
    assert fragment in res.warnings[0] and "--api-url" in res.warnings[0]


def test_an_unreadable_document_leaves_no_address_and_says_so() -> None:
    res = resolve_api(target(), None, document_url=DOC_URL)
    assert "could not be read" in res.warnings[0] and "--api-url" in res.warnings[0]


def test_a_streaming_only_endpoint_is_read_as_sse_and_one_that_also_offers_json_is_not() -> None:
    doc = chat_doc()
    ok = doc["paths"]["/v1/ask"]["post"]["responses"]["200"]
    ok["content"] = {"text/event-stream": {"schema": {"type": "string"}}}
    assert resolved(doc).spec.api.protocol == "sse"  # type: ignore[union-attr]
    ok["content"]["application/json"] = {"schema": {"properties": {"answer": {}}}}
    assert resolved(doc).spec.api.protocol == "rest"  # type: ignore[union-attr]


def test_resolution_is_the_same_every_time() -> None:
    first = resolved(chat_doc()).spec.model_dump(mode="json")
    again = resolved(chat_doc()).spec.model_dump(mode="json")
    assert first == again


# ----------------------------------------------------------------------- an address: the document only says how to talk
def test_an_address_the_owner_gave_is_where_tests_go_whatever_the_document_says() -> None:
    spec = target(url="http://mine.test/prefix/v1/ask")
    res = resolve_api(spec, analyze_openapi(chat_doc(servers=[{"url": "https://other.test"}])), document_url=DOC_URL)
    api = res.spec.api
    assert api is not None and api.url == "http://mine.test/prefix/v1/ask"
    assert api.request_template == {"question": "{{input}}", "conversation_id": "{{session_id}}"}
    assert api.response.output == "$.answer"
    assert res.notes and all(n.startswith("OpenAPI: for POST /v1/ask") for n in res.notes)


def test_what_the_owner_wrote_is_never_replaced() -> None:
    spec = target(
        url="http://mine.test/v1/ask",
        request_template={"q": "{{input}}"},
        protocol="graphql",
        response={"output": "$.data.text", "usage": "$.u"},
    )
    res = resolve_api(spec, analyze_openapi(chat_doc()), document_url=DOC_URL)
    assert res.spec is spec, "nothing to fill in, so the very same object comes back"


def test_left_at_the_default_is_decided_by_value_so_a_stored_or_queued_target_is_resolved_too() -> None:
    """A target that went through the database, a job queue or Redis has every field written; asking "was it set?" would
    treat the defaults as the owner's choice."""
    spec = TargetSpec.model_validate(target(url="http://mine.test/v1/ask").model_dump(mode="json"))
    assert {"request_template", "response", "protocol"} <= spec.api.model_fields_set  # type: ignore[union-attr]
    api = resolve_api(spec, analyze_openapi(chat_doc()), document_url=DOC_URL).spec.api
    assert api is not None and api.request_template["question"] == "{{input}}"
    assert api.request_template != DEFAULT_REQUEST_TEMPLATE


def test_an_endpoint_that_cannot_be_called_as_the_default_template_says_so() -> None:
    doc = chat_doc()
    doc["components"]["schemas"]["AskRequest"]["required"] = ["question", "tenant"]
    doc["components"]["schemas"]["AskRequest"]["properties"]["tenant"] = {"type": "string"}
    res = resolved(doc, target(url="http://mine.test/v1/ask"))
    assert any("requires 'tenant'" in w for w in res.warnings)
    owned = resolved(
        doc, target(url="http://mine.test/v1/ask", request_template={"question": "{{input}}", "tenant": "t1"})
    )
    assert not owned.warnings, "an owner who wrote the request has already answered that"


def test_an_operation_the_document_does_not_describe_changes_nothing() -> None:
    spec = target(url="http://mine.test/somewhere/else")
    assert resolve_api(spec, analyze_openapi(chat_doc()), document_url=DOC_URL).spec is spec
    assert resolve_api(spec, None, document_url=DOC_URL).spec is spec


# ------------------------------------------------------------------------------------------- reading the text
def test_json_and_yaml_documents_are_read_and_a_bomb_is_refused() -> None:
    assert parse_openapi_text('{"openapi": "3.0.0"}') == {"openapi": "3.0.0"}
    assert parse_openapi_text("openapi: 3.0.0\npaths: {}\n") == {"openapi": "3.0.0", "paths": {}}
    assert parse_openapi_text("{openapi: 3.0.0, 'paths': {}}")["paths"] == {}, "YAML flow style that JSON would refuse"
    lines = ["a0: &a0 [x, x, x, x, x, x, x, x, x]"] + [
        f"a{i}: &a{i} [{', '.join([f'*a{i - 1}'] * 9)}]" for i in range(1, 9)
    ]
    with pytest.raises(safeyaml.YamlRefused):
        parse_openapi_text("\n".join(lines))
