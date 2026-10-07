"""``limits.max_retries``: a call is repeated only when it provably never reached the agent.

Repeating a request that may have arrived could repeat an action the agent took (send an email twice, create two tickets), so
only a connection that could not be made is retried. Every other failure is reported once, as it happened.
"""

from __future__ import annotations

import ssl
from collections.abc import Callable

import httpx
import pytest

from agentlab.adapters import http as http_module
from agentlab.adapters.base import AdapterContext
from agentlab.adapters.http import AgentApiAdapter, Retries
from agentlab.core.config import AgentLabConfig
from agentlab.core.models import AgentRequest, AgentResponse, ApiConfig, TargetSpec
from agentlab.providers import ProviderManager
from agentlab.tracing.trace import TraceRecorder

URL = "http://agent.test/chat"


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """The pauses between tries are recorded, not waited for."""
    pauses: list[float] = []

    async def record(seconds: float) -> None:
        pauses.append(seconds)

    monkeypatch.setattr(http_module.asyncio, "sleep", record)
    return pauses


def adapter(handler: Callable[[httpx.Request], httpx.Response], *, retries: int = 2, **api: object) -> AgentApiAdapter:
    config = AgentLabConfig()
    config.limits.max_retries = retries
    spec = TargetSpec(name="agent", api=ApiConfig(url=URL, **api))  # type: ignore[arg-type]
    made = AgentApiAdapter(spec, AdapterContext(config=config))
    made._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    return made


def ask(adapter_: AgentApiAdapter) -> AgentResponse:
    import asyncio

    return asyncio.run(adapter_.send(AgentRequest(input="hello", session_id="s1")))


class Flaky:
    """Fails to connect ``failures`` times, then answers."""

    def __init__(self, failures: int, answer: Callable[[httpx.Request], httpx.Response] | None = None) -> None:
        self.failures = failures
        self.seen: list[str] = []
        self.answer = answer or (lambda request: httpx.Response(200, json={"output": "hi"}))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(str(request.url))
        if len(self.seen) <= self.failures:
            raise httpx.ConnectError("connection refused", request=request)
        return self.answer(request)


# ====================================================================================================== the helper
async def test_a_connection_that_cannot_be_made_is_tried_again_up_to_the_limit(no_waiting: list[float]) -> None:
    retries = Retries(3)
    calls = 0

    async def call() -> str:
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise httpx.ConnectTimeout("slow")
        return "connected"

    assert await retries.run(call) == "connected"
    assert (calls, retries.count) == (3, 2)
    assert no_waiting == [0.25, 0.5], "a short, growing pause between tries"


async def test_it_gives_up_when_the_limit_is_used_and_says_what_happened() -> None:
    retries = Retries(2)
    calls = 0

    async def call() -> str:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("refused")

    with pytest.raises(httpx.ConnectError):
        await retries.run(call)
    assert (calls, retries.count) == (3, 2), "one try and two repeats"


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ReadTimeout("no answer"),  # it may have arrived
        httpx.WriteTimeout("stalled while sending"),  # part of it may have arrived
        httpx.ReadError("connection reset"),
        httpx.RemoteProtocolError("server closed the connection"),
        ValueError("anything else"),
    ],
)
async def test_nothing_that_may_have_reached_the_agent_is_repeated(failure: Exception) -> None:
    retries = Retries(5)
    calls = 0

    async def call() -> str:
        nonlocal calls
        calls += 1
        raise failure

    with pytest.raises(type(failure)):
        await retries.run(call)
    assert (calls, retries.count) == (1, 0)


async def test_zero_switches_retries_off() -> None:
    retries = Retries(0)

    async def call() -> str:
        raise httpx.ConnectError("refused")

    with pytest.raises(httpx.ConnectError):
        await retries.run(call)
    assert retries.count == 0


async def test_a_bad_certificate_is_not_asked_for_again() -> None:
    retries = Retries(4)
    calls = 0

    async def call() -> str:
        nonlocal calls
        calls += 1
        error = httpx.ConnectError("certificate verify failed")
        error.__cause__ = ssl.SSLCertVerificationError("self-signed certificate")
        raise error

    with pytest.raises(httpx.ConnectError):
        await retries.run(call)
    assert calls == 1


async def test_the_pause_never_grows_past_two_seconds(no_waiting: list[float]) -> None:
    retries = Retries(8)

    async def call() -> str:
        raise httpx.PoolTimeout("no free connection")

    with pytest.raises(httpx.PoolTimeout):
        await retries.run(call)
    assert max(no_waiting) == 2.0 and len(no_waiting) == 8


# ============================================================================================== the adapter, REST
def test_a_target_that_comes_up_a_moment_late_is_reached_and_the_retries_are_reported() -> None:
    server = Flaky(failures=2)
    response = ask(adapter(server))
    assert response.error is None and response.output == "hi"
    assert response.retries == 2 and len(server.seen) == 3


def test_a_target_that_stays_down_ends_in_one_clear_error_after_the_limit() -> None:
    server = Flaky(failures=99)
    response = ask(adapter(server, retries=1))
    assert response.error == "connection error: ConnectError"
    assert response.retries == 1 and len(server.seen) == 2


def test_a_run_with_retries_off_asks_once() -> None:
    server = Flaky(failures=99)
    response = ask(adapter(server, retries=0))
    assert response.error == "connection error: ConnectError" and response.retries == 0 and len(server.seen) == 1


def test_an_answer_is_never_asked_for_twice_whatever_it_says() -> None:
    for status in (500, 502, 503, 504, 429, 404):
        server = Flaky(failures=0, answer=lambda request, status=status: httpx.Response(status, json={"error": "no"}))
        response = ask(adapter(server))
        assert response.error == f"HTTP {status}" and response.retries == 0
        assert len(server.seen) == 1, f"HTTP {status} was repeated"


def test_a_timeout_waiting_for_the_answer_is_reported_once() -> None:
    seen: list[str] = []

    def never_answers(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        raise httpx.ReadTimeout("no answer", request=request)

    response = ask(adapter(never_answers))
    assert response.error is not None and response.error.startswith("timeout after") and response.retries == 0
    assert len(seen) == 1


def test_a_redirect_is_followed_and_only_the_hop_that_could_not_connect_is_repeated() -> None:
    log: list[str] = []
    refused = {"left": 1}

    def handler(request: httpx.Request) -> httpx.Response:
        log.append(str(request.url))
        if request.url.path == "/chat":
            return httpx.Response(307, headers={"location": "http://agent.test/v2/chat"})
        if refused["left"]:
            refused["left"] -= 1
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, json={"output": "moved"})

    response = ask(adapter(handler))
    assert response.output == "moved" and response.retries == 1
    assert log == [URL, "http://agent.test/v2/chat", "http://agent.test/v2/chat"], "the first hop was delivered once"


def test_a_credential_is_not_lost_by_a_retry() -> None:
    seen_headers: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers.get("x-test"))
        if len(seen_headers) == 1:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, json={"output": "ok"})

    response = ask(adapter(handler, headers={"X-Test": "same"}))
    assert response.retries == 1 and seen_headers == ["same", "same"]


# ============================================================================================ the adapter, streaming
def stream_answer(request: httpx.Request) -> httpx.Response:
    body = 'data: {"choices":[{"delta":{"content":"he"}}]}\n\ndata: {"choices":[{"delta":{"content":"llo"}}]}\n\ndata: [DONE]\n\n'
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())


def test_a_stream_that_could_not_be_opened_is_opened_again() -> None:
    server = Flaky(failures=1, answer=stream_answer)
    response = ask(adapter(server, protocol="sse"))
    assert response.error is None and response.output == "hello"
    assert response.retries == 1 and len(server.seen) == 2


def test_a_stream_that_stays_unreachable_is_reported_after_the_limit() -> None:
    server = Flaky(failures=99, answer=stream_answer)
    response = ask(adapter(server, retries=1, protocol="sse"))
    assert response.error == "connection error: ConnectError" and response.retries == 1 and len(server.seen) == 2


# ===================================================================================== the same limit for models
def manager(limit: int, provider_retries: int) -> ProviderManager:
    config = AgentLabConfig.model_validate(
        {
            "limits": {"max_retries": limit},
            "providers": [{"name": "mock", "type": "mock", "model": "m", "max_retries": provider_retries}],
        }
    )
    return ProviderManager(config)


def test_the_run_limit_caps_what_a_provider_asks_for() -> None:
    assert manager(limit=0, provider_retries=5).get("mock").config.max_retries == 0
    assert manager(limit=3, provider_retries=5).get("mock").config.max_retries == 3


def test_the_run_limit_never_raises_what_a_provider_asks_for() -> None:
    assert manager(limit=9, provider_retries=2).get("mock").config.max_retries == 2


def test_a_negative_limit_is_refused() -> None:
    with pytest.raises(ValueError, match="max_retries"):
        AgentLabConfig.model_validate({"limits": {"max_retries": -1}})


# ====================================================================================================== the record
def test_the_trace_says_how_often_a_call_was_repeated_and_says_nothing_when_it_was_not() -> None:
    recorder = TraceRecorder("run", "test", 1, None)
    recorder.record_response(1, "s", AgentResponse(output="a", retries=2))
    recorder.record_response(2, "s", AgentResponse(output="b"))
    answers = [e for e in recorder.trace.events if e.type.value == "AgentResponse"]
    assert answers[0].payload["retries"] == 2
    assert "retries" not in answers[1].payload
