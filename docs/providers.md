# Model providers

A *provider* is a connection to a language model. AgentLab uses providers for four things, and only the first
needs one:

1. **The independent judge**: grades quality criteria no rule can check (helpfulness, tone, grounding in context)
   against a rubric. Without a judge those criteria are reported as *not judged*; they are never guessed.
2. **Testing a model as the agent** (`agentlab test --llm ollama:qwen2.5:0.5b`), optionally with a system prompt.
3. **LLM-suggested tests** (`evaluation.llm_test_generation: true`, off by default): the model proposes a few extra
   harmless scenarios. They are marked `llm-suggested` and `unverified` in the plan.
4. **SkillForge** (`agentlab skills forge --suggest`): drafts methodology notes for a capability no skill covers.

Everything else (discovery, test design, execution, deterministic evaluation, scoring, reports) works with no
provider at all.

## Provider types

| `type` | Talks to | Default `base_url` | Key | Notes |
|---|---|---|---|---|
| `ollama` | Ollama's native API | `http://localhost:11434` | none | Capabilities are read from `/api/show`. A trailing `/v1` on the URL is dropped. |
| `lmstudio` | LM Studio's OpenAI-compatible server | `http://localhost:1234/v1` | optional | |
| `vllm` | vLLM's OpenAI-compatible server | `http://localhost:8000/v1` | optional | |
| `llamacpp` | llama.cpp's `llama-server` | `http://localhost:8080/v1` | optional | |
| `openai_compatible` | Any OpenAI-style `/chat/completions` server | **required** | optional | For gateways and servers not listed here. |
| `openai` | OpenAI | `https://api.openai.com/v1` | required | |
| `openrouter` | OpenRouter | `https://openrouter.ai/api/v1` | required | Model discovery returns live pricing and the parameters each model supports, which drive capability negotiation. |
| `gemini` | Google Gemini | `https://generativelanguage.googleapis.com/v1beta` | required | |
| `anthropic` | Anthropic, through the official SDK | the SDK's | required | Needs the `anthropic` extra ([installation.md](installation.md)). |
| `mock` | Nothing: deterministic, scripted | n/a | none | For tests and offline demos. An unscripted judge call answers *uncertain*, so a mock judge can never produce a passing verdict it did not earn. |

All of them implement one interface (`LLMProvider`): chat, streaming, JSON and JSON-schema output, tool calling,
images, embeddings and model discovery. A new provider is a plug-in ([plugins.md](plugins.md)).

## Configuring one

```yaml
providers:
  - name: local                      # the name you refer to elsewhere
    type: ollama
    base_url: http://127.0.0.1:11434
    model: qwen2.5:0.5b

  - name: gateway
    type: openai_compatible
    base_url: https://llm.example.internal/v1
    api_key_ref: env:GATEWAY_API_KEY
    model: my-model

  - name: gemini
    type: gemini
    api_key_ref: env:GEMINI_API_KEY
    model: <a model you can use>
    pricing:                           # optional: USD per million tokens, for cost limits and reports
      <a model you can use>: {input_per_mtok: 0.1, output_per_mtok: 0.4}

evaluation:
  judges:
    - {provider: gemini}               # `model` defaults to the provider's
```

* **Keys are references, never values.** `api_key_ref` is `env:NAME` (read from the environment when needed) or
  `secret:NAME` (a credential stored with `agentlab credentials add`, encrypted). A raw key in the file is refused
  when the provider is first used. Keys are registered with the redactor, so they cannot appear in traces, logs or
  reports even if an error message contains them.
* `timeout` (seconds, 60), `headers`, and `options` (provider-specific; the Anthropic provider reads
  `send_temperature` and `allow_forced_tool_choice`) are optional.
* `capabilities: [chat, json_schema, ...]` overrides what the adapter negotiates, for a model you know better.
* `max_retries` (2) is how often a call that was rate limited or temporarily unavailable is repeated, and it can
  never exceed `limits.max_retries`. A rate-limit answer's `Retry-After` is respected.

## Judges

```yaml
evaluation:
  judges:
    - {provider: strong, model: some-model, weight: 2.0}
    - {provider: other}
  judge_strategy: average              # single | average | vote | min
```

* **A judge is never the model under test.** If a judge is the same provider and model as the target (or the
  target's files name that model), the judge is *disabled for the run* with the reason in the report, rather than
  letting a target grade itself ([ADR 0005](decisions/0005-blocked-is-not-failed-independent-judge.md)). Prefer a
  different model family from the target.
* With several judges the strategy decides the score: `average` (weighted by `weight`), `vote` (the majority of the
  judges that reached the criterion's threshold decides), `min` (the lowest score). When the judges disagree by more
  than 0.4 the result is *uncertain* and its confidence is capped at 0.4.
* The judge is told that everything it reads (the agent's output, documents, tool results) is untrusted data, and it
  answers with a structured verdict (`pass`, `fail` or `uncertain`, a score, a confidence and its reasons). **A judge
  that errors, or is uncertain, never turns a test into a failure by itself**: deterministic checks decide first. When
  nothing else can decide, the test ends in `ERROR` with that reason (it is not scored and is not a failure), and a
  test whose only criteria are judged is `BLOCKED` when no judge is configured
  ([evaluation.md](evaluation.md#the-llm-judge)).
* `agentlab test --no-judge` (or `evaluation.judge_enabled: false`) turns judging off for a run.

## Capabilities degrade, and say so

A provider reports what a model supports. When AgentLab needs something the model lacks (for example JSON-schema
output), it falls back to a weaker mode and records the fallback in the response (`degraded`), or reports the
test as blocked when it cannot be emulated. It does not assume a feature exists.

## Cost

Token usage and cost are recorded per call. Prices come from `pricing:` in the provider, from the provider's own
model listing where it has one (OpenRouter), or from a built-in table for the Anthropic models (marked in the code with
the date it was copied). A model with no known price adds nothing to the cost, and the report says the cost is not
known instead of showing a total that looks complete, so for such models rely on the token limit. The run limits
`limits.max_cost_usd`, `limits.max_test_cost_usd` and `limits.max_tokens` stop a run when they are reached; the status
says which one ([configuration.md](configuration.md)).

## Commands

```bash
agentlab providers list                  # type, endpoint, model, capabilities, whether the key resolves, judge or not
agentlab providers check local           # a model listing and one tiny completion: may be billed, runs only when asked
agentlab models list                     # the models each configured provider reports
agentlab doctor --live                   # also contacts every remote provider
```

## What has been verified

Be careful to read this as written:

| Provider | Verified against |
|---|---|
| `ollama` | A live Ollama server with a small model (`qwen2.5:0.5b`): listing, chat, streaming, structured output, and a full evaluation using it as the judge and as the model under test. |
| `openai_compatible`, `openai`, `openrouter`, `lmstudio`, `vllm`, `llamacpp` | A local server speaking the OpenAI wire format (requests, streaming, tool calls, JSON mode, OpenRouter's model listing). **Not** against the hosted services. |
| `gemini` | A local stand-in for the `generateContent` API, including the bad-key error path. **Not** against Google's service. |
| `anthropic` | The official SDK pointed at a local stand-in (structured output, tools, pricing). **Not** against Anthropic's service. |
| `mock` | Everything that uses it. |

The hosted services were not called because no keys were available when this was built and AgentLab never stores
or invents any. The adapters follow each vendor's documented request format; the first real call is what proves
one against your account, so run `agentlab providers check <name>` before relying on a judge.
