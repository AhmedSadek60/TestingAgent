import json

import pytest

from agentlab.core.config import Pricing, ProviderConfig
from agentlab.core.errors import CredentialError, ProviderError, UnsupportedCapability
from agentlab.providers import (
    Capability,
    CompletionRequest,
    Message,
    ToolSpec,
    create_provider,
    parse_json_loose,
)
from agentlab.security.redactor import get_redactor
from tests.support.fake_providers import Recorder, anthropic_app, gemini_app, ollama_app, openai_app
from tests.support.servers import serve

SCHEMA = {
    "type": "object",
    "properties": {"verdict": {"type": "string"}, "score": {"type": "number"}},
    "required": ["verdict", "score"],
    "additionalProperties": False,
}
TOOL = ToolSpec(name="lookup", description="d", parameters={"type": "object", "properties": {"q": {"type": "string"}}})


def req(**kw):
    return CompletionRequest(
        messages=[Message(role="system", content="be brief"), Message(role="user", content="hi there")], **kw
    )


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TEST_KEY", "sk-test-openai-key-123456")
    monkeypatch.setenv("TEST_GEMINI_KEY", "AIza-test-gemini-key")
    monkeypatch.setenv("TEST_ANTHROPIC_KEY", "sk-ant-test-key-1234567890")


async def test_openai_compatible_chat_json_tools_stream():
    rec = Recorder()
    with serve(openai_app(rec)) as srv:
        p = create_provider(
            ProviderConfig(
                name="oa",
                type="openai",
                base_url=srv.url + "/v1",
                api_key_ref="env:TEST_KEY",
                model="gpt-test",
                pricing={"gpt-test": Pricing(input_per_mtok=1.0, output_per_mtok=2.0)},
            )
        )
        r = await p.complete(req())
        assert r.text == "echo: hi there" and r.usage.input_tokens == 11 and r.usage.output_tokens == 7
        assert r.cost_usd == pytest.approx((11 * 1 + 7 * 2) / 1e6)
        assert rec.requests[0]["headers"]["authorization"] == "Bearer sk-test-openai-key-123456"
        j = await p.complete(req(json_schema=SCHEMA))
        assert j.parsed == {"verdict": "pass", "score": 0.9}
        assert rec.requests[-1]["body"]["response_format"]["type"] == "json_schema"
        t = await p.complete(req(tools=[TOOL], tool_choice="required"))
        assert t.tool_calls[0].name == "lookup" and t.tool_calls[0].arguments == {"q": "x"}
        assert "".join([c async for c in p.stream(req())]) == "hello"
        assert [m.id for m in await p.discover()] == ["gpt-test"]
        assert len(await p.embed(["a", "b"])) == 2
        await p.aclose()


async def test_openrouter_discovery_drives_capability_negotiation_and_reports_cost():
    rec = Recorder()
    with serve(openai_app(rec, openrouter=True)) as srv:
        p = create_provider(
            ProviderConfig(
                name="or",
                type="openrouter",
                base_url=srv.url + "/v1",
                api_key_ref="env:TEST_KEY",
                model="vendor/plain-model",
            )
        )
        models = {m.id: m for m in await p.discover()}
        assert Capability.TOOL_CALLING in models["vendor/tool-model"].capabilities
        assert Capability.JSON_SCHEMA in models["vendor/tool-model"].capabilities
        assert Capability.MULTIMODAL in models["vendor/tool-model"].capabilities
        assert models["vendor/tool-model"].pricing.input_per_mtok == pytest.approx(1.0)
        assert p.supports(Capability.TOOL_CALLING, "vendor/tool-model")
        assert not p.supports(Capability.TOOL_CALLING, "vendor/plain-model")
        # plain model has no tool support: refuse instead of silently assuming
        with pytest.raises(UnsupportedCapability):
            await p.complete(req(tools=[TOOL]))
        # no json_schema support: degrade gracefully with prompt-level JSON + validation
        r = await p.complete(req(json_schema=SCHEMA))
        assert "json_schema" in r.degraded and r.parsed["verdict"] == "pass"
        assert r.cost_usd == pytest.approx(0.00042)  # provider-reported cost wins
        assert rec.requests[-1]["body"]["usage"] == {"include": True}


async def test_gemini_contract():
    rec = Recorder()
    with serve(gemini_app(rec)) as srv:
        p = create_provider(
            ProviderConfig(
                name="g",
                type="gemini",
                base_url=srv.url + "/v1beta",
                api_key_ref="env:TEST_GEMINI_KEY",
                model="gem-chat",
            )
        )
        r = await p.complete(req(json_schema=SCHEMA, max_tokens=50))
        body = rec.requests[-1]["body"]
        assert rec.requests[-1]["headers"]["x-goog-api-key"] == "AIza-test-gemini-key"
        assert body["systemInstruction"] == {"parts": [{"text": "be brief"}]}
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        assert body["generationConfig"]["responseJsonSchema"] == SCHEMA
        assert body["generationConfig"]["maxOutputTokens"] == 50
        assert r.parsed["score"] == 0.8 and r.usage.output_tokens == 7  # candidates + thoughts
        t = await p.complete(req(tools=[TOOL]))
        assert t.tool_calls[0].name == "lookup"
        assert "".join([c async for c in p.stream(req())]) == "abcd"
        models = {m.id: m for m in await p.discover()}
        assert Capability.TOOL_CALLING in models["gem-chat"].capabilities
        assert Capability.EMBEDDINGS in models["gem-embed"].capabilities
        assert Capability.CHAT not in models["gem-embed"].capabilities


async def test_gemini_bad_key_is_credential_error():
    rec = Recorder()
    with serve(gemini_app(rec)) as srv:
        import os

        os.environ["WRONG_KEY"] = "wrong-key-value"
        p = create_provider(
            ProviderConfig(
                name="g", type="gemini", base_url=srv.url + "/v1beta", api_key_ref="env:WRONG_KEY", model="gem-chat"
            )
        )
        with pytest.raises(CredentialError):
            await p.complete(req())


async def test_ollama_native_capabilities_from_show():
    rec = Recorder()
    with serve(ollama_app(rec)) as srv:
        p = create_provider(ProviderConfig(name="ol", type="ollama", base_url=srv.url, model="tiny:latest"))
        models = {m.id: m for m in await p.discover()}
        assert Capability.TOOL_CALLING in models["llama-tools:latest"].capabilities
        assert Capability.TOOL_CALLING not in models["tiny:latest"].capabilities
        with pytest.raises(UnsupportedCapability):
            await p.complete(req(tools=[TOOL]))
        r = await p.complete(req(json_schema=SCHEMA))
        assert rec.requests[-1]["body"]["format"] == SCHEMA and r.parsed["score"] == 0.7
        assert r.usage.input_tokens == 4 and r.cost_usd == 0.0
        p2 = create_provider(
            ProviderConfig(name="ol2", type="ollama", base_url=srv.url + "/v1", model="llama-tools:latest")
        )
        await p2.discover()
        t = await p2.complete(req(tools=[TOOL]))
        assert t.tool_calls[0].arguments == {"q": "x"}
        assert "".join([c async for c in p2.stream(req())]) == "hello"


async def test_anthropic_sdk_contract_structured_output_tools_and_pricing():
    rec = Recorder()
    with serve(anthropic_app(rec)) as srv:
        p = create_provider(
            ProviderConfig(
                name="an",
                type="anthropic",
                base_url=srv.url,
                api_key_ref="env:TEST_ANTHROPIC_KEY",
                model="claude-opus-5-5",
            )
        )
        r = await p.complete(req(json_schema=SCHEMA, temperature=0.0))
        body = rec.requests[-1]["body"]
        assert body["system"] == "be brief"
        assert body["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}}
        assert "temperature" not in body  # recent models reject sampling params
        assert r.parsed == {"verdict": "pass", "score": 0.95}
        assert r.usage.input_tokens == 15  # includes cache-read tokens
        assert r.cost_usd == pytest.approx((15 * 4 + 6 * 20) / 1e6)
        t = await p.complete(req(tools=[TOOL], tool_choice="required"))
        body = rec.requests[-1]["body"]
        assert body["tools"][0]["input_schema"] == TOOL.parameters
        assert body["tool_choice"] == {"type": "auto"} and "forced_tool_choice" in t.degraded
        assert t.tool_calls[0].name == "lookup"
        assert [m.id for m in await p.discover()] == ["claude-opus-5-5"]
        await p.aclose()


async def test_retry_accounting_and_failure_kinds():
    rec = Recorder()
    rec.fail_with = [503, 503]
    with serve(openai_app(rec)) as srv:
        p = create_provider(
            ProviderConfig(
                name="oa",
                type="openai",
                base_url=srv.url + "/v1",
                api_key_ref="env:TEST_KEY",
                model="m",
                max_retries=2,
                options={"backoff_base": 0},
            )
        )
        r = await p.complete(req())
        assert r.retries == 2
        rec.fail_with = [503, 503, 503]
        with pytest.raises(ProviderError):
            await p.complete(req())
        rec.fail_with = [429, 429, 429]
        from agentlab.core.errors import RateLimitError

        with pytest.raises(RateLimitError):
            await p.complete(req())


async def test_api_keys_are_registered_with_redactor_and_never_in_errors():
    rec = Recorder()
    with serve(openai_app(rec)) as srv:
        p = create_provider(
            ProviderConfig(name="oa", type="openai", base_url=srv.url + "/v1", api_key_ref="env:TEST_KEY", model="m")
        )
        await p.complete(req())
        assert get_redactor().redact_text("token=sk-test-openai-key-123456")[0] == "token=[REDACTED:env:TEST_KEY]"


def test_custom_provider_requires_base_url_and_raw_keys_rejected():
    with pytest.raises(ProviderError):
        create_provider(ProviderConfig(name="x", type="openai_compatible", model="m"))
    p = create_provider(
        ProviderConfig(
            name="x",
            type="openai_compatible",
            base_url="http://x/v1",
            model="m",
            api_key_ref="sk-raw-secret-should-not-be-here",
        )
    )
    with pytest.raises(CredentialError):
        p.api_key()


async def test_mock_provider_scripting_and_unscripted_judge_is_uncertain():
    from agentlab.providers.mock import MockProvider

    m = MockProvider(ProviderConfig(name="mock", type="mock", model="mock"))
    m.when(r"capital of france", "Paris").enqueue({"tool_calls": [{"name": "lookup", "arguments": {"q": "z"}}]})
    first = await m.complete(CompletionRequest(messages=[Message(role="user", content="whatever")], tools=[TOOL]))
    assert first.tool_calls[0].arguments == {"q": "z"}
    assert (
        await m.complete(CompletionRequest(messages=[Message(role="user", content="capital of France?")]))
    ).text == "Paris"
    schema = {"type": "object", "properties": {"score": {"type": "number"}, "uncertain": {"type": "boolean"}}}
    j = await m.complete(CompletionRequest(messages=[Message(role="user", content="judge")], json_schema=schema))
    assert j.parsed["uncertain"] is True


def test_parse_json_loose():
    assert parse_json_loose('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_loose('Sure! Here: {"a": {"b": "}"}} thanks') == {"a": {"b": "}"}}
    assert parse_json_loose("no json") is None
    assert json.loads(json.dumps(parse_json_loose('{"x":1}'))) == {"x": 1}
