"""The ways an HTTP agent can be spoken to: REST with a request template and JSONPath mapping, GraphQL and server-sent events.

WebSocket agent endpoints are not supported, and the adapter says so instead of pretending. The agents here are stubs behind
``httpx.MockTransport``: they check what was sent and answer in the shapes real agents use.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import httpx
import pytest

from agentlab.adapters.base import AdapterContext
from agentlab.adapters.http import AgentApiAdapter
from agentlab.core.config import AgentLabConfig
from agentlab.core.errors import UnsupportedCapability
from agentlab.core.models import AgentRequest, AgentResponse, ApiConfig, TargetSpec

URL = "http://agent.test/api"


def adapter(handler: Callable[[httpx.Request], httpx.Response], **api: object) -> AgentApiAdapter:
    spec = TargetSpec(name="agent", api=ApiConfig(url=URL, **api))  # type: ignore[arg-type]
    made = AgentApiAdapter(spec, AdapterContext(config=AgentLabConfig()))
    made._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    return made


def ask(made: AgentApiAdapter, text: str = "hello", **extra: object) -> AgentResponse:
    request = AgentRequest(input=text, session_id="s-1", **extra)  # type: ignore[arg-type]
    return asyncio.run(made.send(request))


# ================================================================================================================ REST
def test_the_request_is_the_template_filled_in_and_the_answer_is_found_by_jsonpath() -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(
            200,
            json={
                "result": {
                    "text": "Paris",
                    "calls": [{"name": "lookup", "arguments": {"q": "capital"}, "result": "Paris"}],
                },
                "sources": [{"source": "geo.md", "content": "Paris is the capital."}],
                "usage": {"input_tokens": 12, "output_tokens": 3, "cost_usd": 0.001},
            },
        )

    made = adapter(
        handler,
        request_template={"message": {"text": "{{input}}"}, "conversation": "{{session_id}}"},
        response={"output": "$.result.text", "tool_calls": "$.result.calls", "contexts": "$.sources"},
    )
    response = ask(made, "What is the capital of France?")
    assert json.loads(sent[0].content) == {"message": {"text": "What is the capital of France?"}, "conversation": "s-1"}
    assert response.output == "Paris" and response.error is None
    assert [(c.name, c.arguments) for c in response.tool_calls] == [("lookup", {"q": "capital"})]
    assert [c.source for c in response.contexts] == ["geo.md"]
    assert (response.usage.input_tokens, response.usage.output_tokens) == (12, 3)


def test_an_openai_shaped_answer_needs_no_mapping() -> None:
    body = {
        "choices": [
            {"message": {"content": "Hi there", "tool_calls": [{"function": {"name": "f", "arguments": '{"a": 1}'}}]}}
        ]
    }
    response = ask(adapter(lambda request: httpx.Response(200, json=body)))
    assert response.output == "Hi there"
    assert [(c.name, c.arguments) for c in response.tool_calls] == [("f", {"a": 1})]


def test_the_session_can_travel_in_a_header() -> None:
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("x-session"))
        return httpx.Response(200, json={"output": "ok"})

    ask(adapter(handler, session_header="X-Session"))
    assert seen == ["s-1"]


def test_a_plain_text_agent_is_fine_and_a_broken_json_one_is_a_defect() -> None:
    plain = ask(adapter(lambda r: httpx.Response(200, text="just text", headers={"content-type": "text/plain"})))
    assert plain.output == "just text" and plain.error is None
    broken = ask(adapter(lambda r: httpx.Response(200, text="{not json", headers={"content-type": "application/json"})))
    assert broken.error == "the response is declared as JSON but is not valid JSON"


def test_an_error_status_is_an_error_with_a_redacted_snippet() -> None:
    secret = "sk-" + "a" * 30
    response = ask(adapter(lambda r: httpx.Response(500, text=f"boom, key {secret}")))
    assert response.error == "HTTP 500" and response.status_code == 500
    assert secret not in str(response.raw)


# ============================================================================================================ GraphQL
QUERY = "query Ask($input: String!) { ask(input: $input) { answer } }"


def test_graphql_sends_the_query_with_the_template_as_variables_and_reads_the_data() -> None:
    sent: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"data": {"ask": {"answer": "Berlin"}}})

    made = adapter(
        handler,
        protocol="graphql",
        graphql_query=QUERY,
        request_template={"input": "{{input}}"},
        response={"output": "$.data.ask.answer"},
    )
    response = ask(made, "Capital of Germany?")
    assert sent == [{"query": QUERY, "variables": {"input": "Capital of Germany?"}}]
    assert response.output == "Berlin" and response.error is None


def test_graphql_errors_arrive_with_status_200_and_are_not_taken_for_an_empty_answer() -> None:
    body = {"errors": [{"message": "Cannot query field ask on type Query"}], "data": None}
    made = adapter(
        lambda r: httpx.Response(200, json=body),
        protocol="graphql",
        graphql_query=QUERY,
        response={"output": "$.data.ask.answer"},
    )
    response = ask(made)
    assert response.output == ""
    assert response.error is not None and "Cannot query field ask" in response.error


def test_graphql_that_answers_despite_an_error_keeps_the_answer() -> None:
    body = {"errors": [{"message": "a field was slow"}], "data": {"ask": {"answer": "partial"}}}
    made = adapter(
        lambda r: httpx.Response(200, json=body),
        protocol="graphql",
        graphql_query=QUERY,
        response={"output": "$.data.ask.answer"},
    )
    response = ask(made)
    assert response.output == "partial" and response.error is None


# ================================================================================================================== SSE
def events(*lines: str) -> httpx.Response:
    return httpx.Response(
        200, headers={"content-type": "text/event-stream"}, content=("\n".join(lines) + "\n").encode()
    )


def test_a_stream_is_joined_and_its_tool_calls_and_usage_are_kept() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept"] == "text/event-stream"
        return events(
            'data: {"choices":[{"delta":{"content":"Hel"}}]}',
            "",
            'data: {"type":"tool_call","name":"lookup","arguments":{"q":"x"},"result":"1"}',
            "",
            'data: {"choices":[{"delta":{"content":"lo"}}]}',
            "",
            'data: {"type":"usage","input_tokens":5,"output_tokens":2,"cost_usd":0.002}',
            "",
            "data: [DONE]",
            "",
        )

    response = ask(adapter(handler, protocol="sse"))
    assert response.output == "Hello" and response.error is None
    assert [(c.name, c.arguments) for c in response.tool_calls] == [("lookup", {"q": "x"})]
    assert (response.usage.input_tokens, response.usage.output_tokens, response.usage.cost_usd) == (5, 2, 0.002)
    assert response.first_token_ms is not None


def test_a_stream_that_is_refused_is_an_error() -> None:
    response = ask(adapter(lambda r: httpx.Response(503), protocol="sse"))
    assert response.error == "HTTP 503" and response.output == ""


def test_text_that_is_not_json_in_a_stream_is_kept_as_text() -> None:
    response = ask(adapter(lambda r: events("data: plain words", "", "data: [DONE]", ""), protocol="sse"))
    assert response.output == "plain words"


# ========================================================================================================== WebSocket
def test_a_websocket_endpoint_is_refused_up_front_and_the_reason_names_the_alternatives() -> None:
    spec = TargetSpec(name="agent", api=ApiConfig(url="ws://agent.test/socket", protocol="websocket"))
    with pytest.raises(UnsupportedCapability, match="WebSocket.*not supported.*REST, SSE or GraphQL"):
        AgentApiAdapter(spec, AdapterContext(config=AgentLabConfig()))
