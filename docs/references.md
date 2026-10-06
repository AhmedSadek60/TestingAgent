# External references

**Date researched:** 2026-10-05

This is a curated list of external sources that informed AgentLab's design: its provider adapters, judge engine, trajectory and RAG evaluation, security test suites (prompt injection, MCP, sandbox), Playwright browser engine, skills format and tracing. An entry here means the source **informed the design**. It does **not** mean code was copied. No third-party code was copied into AgentLab.

On 2026-10-05 every URL below returned a live page (HTTP 200 after redirects) or was confirmed through the GitHub repository search API. Facts about open-source projects come from GitHub repository metadata and the PyPI or npm registries on that date:

- **License:** the SPDX license ID.
- **Last push:** the date of the most recent push to the repository.
- **Latest release:** the most recent package release.

All of these change over time, so re-check them before relying on them.

Sources are ordered by preference: (1) official vendor docs, (2) standards and security organizations, (3) high-quality open source, (4) academic papers, (5) community writing.

---

## 1. LLM provider APIs (provider adapters)

| # | Title | URL | Source type | What AgentLab took from it |
|---|---|---|---|---|
| 1 | Gemini API: Generating content (`generateContent` reference) | https://ai.google.dev/api/generate-content | Official docs | Request and response shape for the Gemini adapter: `contents`/`parts`, `systemInstruction`, and `GenerationConfig` fields including `responseMimeType`, `responseSchema` and `responseJsonSchema` for structured judge output. |
| 2 | Gemini API: Structured output | https://ai.google.dev/gemini-api/docs/structured-output | Official docs | How to request schema-constrained JSON. The guide now also shows the newer Interactions-style `response_format`. The adapter isolates this choice behind one "structured output" capability flag. |
| 3 | Gemini API: Function calling | https://ai.google.dev/gemini-api/docs/function-calling | Official docs | Declaring tools and parsing `functionCall` / `functionResponse` parts into AgentLab's provider-neutral tool-call model. |
| 4 | Gemini API: Models (list/get reference) | https://ai.google.dev/api/models | Official docs | Model discovery (`models.list`, `supportedGenerationMethods`, token limits) for capability detection. |
| 5 | Gemini API: Embeddings | https://ai.google.dev/gemini-api/docs/embeddings | Official docs | Embedding calls used by RAG metrics such as semantic similarity of answer and context. |
| 6 | OpenRouter API: Create a chat completion | https://openrouter.ai/docs/api/api-reference/chat/create-a-chat-completion | Official docs | The OpenAI-compatible request shape plus OpenRouter-specific fields (provider routing, usage accounting) for the OpenRouter adapter. |
| 7 | OpenRouter API: List all models and their properties | https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties | Official docs | `pricing` and `supported_parameters` per model. AgentLab uses them for cost estimates and to check that a model supports `tools` / `response_format` before running a suite. |
| 8 | OpenRouter: Structured outputs | https://openrouter.ai/docs/guides/features/structured-outputs | Official docs | `response_format: json_schema` behavior on OpenRouter, and the fallback when a routed model lacks support. |
| 9 | OpenRouter: Tool calling | https://openrouter.ai/docs/guides/features/tool-calling | Official docs | How tool calls are normalized across upstream providers. |
| 10 | OpenAI API reference: Chat Completions | https://developers.openai.com/api/reference/resources/chat | Official docs | The canonical Chat Completions schema. It is the "OpenAI-compatible" base class reused by the OpenRouter, LM Studio, vLLM and llama.cpp adapters. |
| 11 | OpenAI: Structured outputs guide | https://developers.openai.com/api/docs/guides/structured-outputs | Official docs | `json_schema` with `strict: true` and the subset of JSON Schema that is supported. Judge rubrics are written to stay inside that subset. |
| 12 | OpenAI: Function calling guide | https://developers.openai.com/api/docs/guides/function-calling | Official docs | Tool definitions, parallel tool calls and strict function schemas for the tool-call normalizer. |
| 13 | Anthropic: Messages API | https://platform.claude.com/docs/en/api/messages | Official docs | Request and response shape for the Anthropic adapter: content blocks, `stop_reason`, usage. |
| 14 | Anthropic: Tool use overview | https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview | Official docs | `tool_use` / `tool_result` blocks mapped into AgentLab's provider-neutral trajectory steps. |
| 15 | Anthropic: Structured outputs | https://platform.claude.com/docs/en/build-with-claude/structured-outputs | Official docs | JSON outputs via `output_config.format` (`type: "json_schema"`) and strict tool use (`strict: true`). Used for structured judge verdicts on Claude models. |
| 16 | Anthropic: Models API (list models) | https://platform.claude.com/docs/en/api/models/list | Official docs | Model discovery for the Anthropic adapter. |
| 17 | Ollama API: Generate a chat message (`/api/chat`) | https://docs.ollama.com/api/chat | Official docs | Local-model adapter: `messages`, `tools` and `format` (accepts a JSON schema) on `/api/chat`. |
| 18 | Ollama: Structured outputs | https://docs.ollama.com/capabilities/structured-outputs | Official docs | Passing a JSON schema in `format` for local judges. |
| 19 | Ollama API: List models (`/api/tags`) | https://docs.ollama.com/api/tags | Official docs | Listing installed local models. |
| 20 | Ollama API: Show model details (`/api/show`) | https://docs.ollama.com/api-reference/show-model-details | Official docs | The `capabilities` array (e.g. `completion`, `tools`, `thinking`, `vision`) used to gate test suites by model capability. |
| 21 | LM Studio: OpenAI compatibility endpoints | https://lmstudio.ai/docs/developer/openai-compat | Official docs | Treat LM Studio as an OpenAI-compatible base URL. |
| 22 | LM Studio: Structured output | https://lmstudio.ai/docs/developer/openai-compat/structured-output | Official docs | Support for `response_format` JSON schema in LM Studio. |
| 23 | vLLM: OpenAI-compatible server | https://docs.vllm.ai/en/latest/serving/openai_compatible_server/ | Official docs (OSS project: vllm-project/vllm, Apache-2.0; last push 2026-10-05; PyPI 0.31.0, 2026-10-05) | Self-hosted OpenAI-compatible endpoint behavior and the extra sampling parameters it accepts. |
| 24 | vLLM: Structured outputs | https://docs.vllm.ai/en/latest/features/structured_outputs/ | Official docs | Guided/structured decoding options for self-hosted judge models. |
| 25 | llama.cpp server README | https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md | Open-source project (MIT; last push 2026-10-05, very active) | `/v1/chat/completions` compatibility and schema/grammar-constrained JSON through `response_format`. Covers the lowest-footprint local backend. |

## 2. Agent evaluation and trajectory evaluation (judge engine, trajectory evaluation)

| # | Title | URL | Source type | What AgentLab took from it |
|---|---|---|---|---|
| 26 | Anthropic: Demystifying evals for AI agents | https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents | Official docs (vendor engineering blog) | Vocabulary and structure for agent evals: tasks, trials, graders (code-based, model-based, human), and outcome versus transcript grading. Shapes how AgentLab separates a suite, a run and a grader. |
| 27 | Anthropic: Building effective agents | https://www.anthropic.com/engineering/building-effective-agents | Official docs (vendor engineering blog) | Taxonomy of workflow and agent patterns, used to label the agent-under-test archetypes AgentLab's scenarios target. |
| 28 | Anthropic: Writing effective tools for agents | https://www.anthropic.com/engineering/writing-tools-for-agents | Official docs (vendor engineering blog) | Evaluation-driven tool design. Informs the tool-use checks in trajectory evaluation (wrong tool, redundant calls, bad arguments). |
| 29 | OpenAI: Evals guide | https://developers.openai.com/api/docs/guides/evals | Official docs | Shape of an eval: data source, testing criteria, graders. |
| 30 | OpenAI: Evaluation best practices | https://developers.openai.com/api/docs/guides/evaluation-best-practices | Official docs | Guidance on eval design, sampling and avoiding grader leakage. Used in judge-engine defaults. |
| 31 | LangSmith: Trajectory evaluations | https://docs.langchain.com/langsmith/trajectory-evals | Official docs | Trajectory match modes (strict, unordered, subset/superset) and LLM-as-judge over trajectories. These are AgentLab's trajectory comparators. |
| 32 | agentevals (LangChain) | https://github.com/langchain-ai/agentevals | Open-source project (MIT; last push 2026-07-14; PyPI 0.0.9, 2025-07-24; low release cadence) | Reference semantics for trajectory matchers. Design reference only. |
| 33 | Inspect AI (UK AI Security Institute) | https://inspect.aisi.org.uk/ | Open-source project / government lab (MIT; repo UKGovernmentBEIS/inspect_ai last push 2026-10-05; PyPI 0.3.276, 2026-10-02; very active) | Task / solver / scorer separation, sandboxed agent tool execution, and log format. Strong influence on AgentLab's run model. See also [Agents](https://inspect.aisi.org.uk/agents.html) and [Scorers](https://inspect.aisi.org.uk/scorers.html). |
| 34 | DeepEval: Metrics introduction | https://deepeval.com/docs/metrics-introduction | Open-source project (confident-ai/deepeval, Apache-2.0; last push 2026-10-05; PyPI 4.2.8, 2026-10-02; active) | Metric interface (score, threshold, reason) and agentic metrics such as [Tool Correctness](https://deepeval.com/docs/metrics-tool-correctness) and [Task Completion](https://deepeval.com/docs/metrics-task-completion). |
| 35 | Arize Phoenix: LLM evals | https://arize.com/docs/phoenix/evaluation/llm-evals | Open-source project (Arize-ai/phoenix, **Elastic License 2.0**, source-available rather than OSI open source; last push 2026-10-05; PyPI 20.19.0, 2026-10-01) | Tracing-plus-evals UX and OTel-based trace ingestion. Design reference only; the ELv2 license restricts offering it as a managed service. |
| 36 | τ-bench: Tool-Agent-User Interaction in Real-World Domains (Yao et al., 2024) | https://arxiv.org/abs/2406.12045 | Academic paper | Database-state outcome checking and the pass^k reliability metric across repeated trials. AgentLab reports pass^k next to pass@1. |
| 37 | τ²-Bench: Conversational Agents in a Dual-Control Environment (2025) | https://arxiv.org/abs/2506.07982 | Academic paper (code: sierra-research/tau2-bench, MIT; last push 2026-09-28; PyPI `tau2` 2.4.0, 2026-08-21) | Simulated users who can also act on the environment. Informs AgentLab's simulated-user scenarios. |
| 38 | AgentBench: Evaluating LLMs as Agents (Liu et al., 2023) | https://arxiv.org/abs/2308.03688 | Academic paper (code: THUDM/AgentBench, Apache-2.0; last push 2026-02-08) | Multi-environment agent evaluation and failure taxonomy (invalid format, invalid action, task limit exceeded). |
| 39 | WebArena: A Realistic Web Environment for Building Autonomous Agents (Zhou et al., 2023) | https://arxiv.org/abs/2307.13854 | Academic paper (site https://webarena.dev/; code web-arena-x/webarena, Apache-2.0; last push 2025-11-26) | Functional-correctness checks of final web state. Informs browser-task assertions in the Playwright engine. |
| 40 | SWE-bench: Can LMs Resolve Real-World GitHub Issues? (Jimenez et al., 2023) | https://arxiv.org/abs/2310.06770 | Academic paper (site https://www.swebench.com/; code SWE-bench/SWE-bench, MIT; last push 2026-09-18; PyPI 5.0.2, 2026-08-18) | Execution-based grading in containerized harnesses: tests decide pass/fail, not a judge. |
| 41 | GAIA: a benchmark for General AI Assistants (Mialon et al., 2023) | https://arxiv.org/abs/2311.12983 | Academic paper (dataset: https://huggingface.co/datasets/gaia-benchmark/GAIA, gated) | Short, unambiguous answers that allow exact-match scoring of multi-step assistant tasks. |
| 42 | Berkeley Function Calling Leaderboard (BFCL) | https://gorilla.cs.berkeley.edu/leaderboard.html | Academic project (ShishirPatil/gorilla, Apache-2.0; last push 2026-04-13) | AST-based function-call checking and multi-turn categories ([BFCL v3 blog](https://gorilla.cs.berkeley.edu/blogs/13_bfcl_v3_multi_turn.html)). Informs AgentLab's argument-level tool-call matcher. |
| 43 | MCP-Bench: Benchmarking Tool-Using LLM Agents via MCP Servers (2025) | https://arxiv.org/abs/2508.20453 | Academic paper (code: Accenture/mcp-bench; no license detected in repo metadata, so do not reuse; last push 2025-10-07) | Evaluating agents against real MCP servers, including tool-schema understanding and multi-hop planning. Informs AgentLab's MCP scenario design. |
| 44 | Agent-as-a-Judge: Evaluate Agents with Agents (Zhuge et al., 2024) | https://arxiv.org/abs/2410.10934 | Academic paper | Judging intermediate steps rather than only final outputs. Supports the trajectory-aware judge mode. |
| 45 | OpenAI Evals (framework and registry) | https://github.com/openai/evals | Open-source project (README states MIT; GitHub metadata shows NOASSERTION; last push 2026-04-14; reduced activity) | Early registry-of-evals pattern (YAML spec plus grader). Historical design reference only. |

## 3. RAG metrics

| # | Title | URL | Source type | What AgentLab took from it |
|---|---|---|---|---|
| 46 | Ragas: Available metrics | https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/ | Open-source project (repo now at github.com/vibrantlabsai/ragas, Apache-2.0; last push 2026-02-24; PyPI 0.4.3, 2026-01-13; slower cadence in 2026) | Definitions of context precision, context recall, response relevancy and noise sensitivity, implemented independently in AgentLab's RAG metric module. See also [Faithfulness](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/). |
| 47 | Ragas: Automated Evaluation of Retrieval Augmented Generation (Es et al., 2023) | https://arxiv.org/abs/2309.15217 | Academic paper | Claim decomposition, then verification against context, as the method for faithfulness scoring. |
| 48 | TruLens: The RAG Triad | https://www.trulens.org/getting_started/core_concepts/rag_triad/ | Open-source project (truera/trulens, MIT; last push 2026-10-05; PyPI 2.15.0, 2026-10-05; active) | The triad of context relevance, groundedness and answer relevance as AgentLab's default RAG report layout. |

## 4. LLM-as-a-judge (judge engine)

| # | Title | URL | Source type | What AgentLab took from it |
|---|---|---|---|---|
| 49 | Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena (Zheng et al., 2023) | https://arxiv.org/abs/2306.05685 | Academic paper (code: lm-sys/FastChat, Apache-2.0; last push 2026-05-01) | Pairwise vs. single-answer grading and the known position, verbosity and self-enhancement biases. Leads to swap-order pairwise judging and reference-guided grading. |
| 50 | G-Eval: NLG Evaluation using GPT-4 with Better Human Alignment (Liu et al., 2023) | https://arxiv.org/abs/2303.16634 | Academic paper | Rubric plus auto-generated evaluation steps (chain-of-thought) before the score, and form-filling output. Basis of AgentLab's rubric judge template. |
| 51 | Large Language Models are not Fair Evaluators (Wang et al., 2023) | https://arxiv.org/abs/2305.17926 | Academic paper | Position-bias evidence and calibration by balanced position swapping. AgentLab judges both orders and flags disagreement. |
| 52 | Judging the Judges: A Systematic Study of Position Bias in LLM-as-a-Judge (2024) | https://arxiv.org/abs/2406.07791 | Academic paper | Metrics for repetition stability and position consistency, reported as judge-reliability diagnostics. |
| 53 | A Survey on LLM-as-a-Judge (Gu et al., 2024) | https://arxiv.org/abs/2411.15594 | Academic paper | Overview of judge reliability techniques: multiple judges, explicit rubrics, structured output, human agreement checks. |

## 5. Security: prompt injection and agent threats (prompt-injection tests, red-team suites)

| # | Title | URL | Source type | What AgentLab took from it |
|---|---|---|---|---|
| 54 | OWASP Top 10 for LLM Applications (2025) | https://genai.owasp.org/llm-top-10/ | Standards / security org | Primary taxonomy for tagging AgentLab security findings (LLM01 Prompt Injection, LLM02 Sensitive Information Disclosure, LLM06 Excessive Agency, LLM07 System Prompt Leakage, etc.). |
| 55 | OWASP Top 10 for Agentic Applications for 2026 | https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/ | Standards / security org | Agent-specific risk categories (goal hijack, tool misuse, identity/privilege abuse, memory poisoning and so on) mapped to AgentLab agent security tests. |
| 56 | OWASP Agentic AI: Threats and Mitigations | https://genai.owasp.org/resource/agentic-ai-threats-and-mitigations/ | Standards / security org | Threat-model reference for agent architectures, from the OWASP [Agentic Security Initiative](https://genai.owasp.org/initiatives/agentic-security-initiative/). |
| 57 | MITRE ATLAS | https://atlas.mitre.org/ | Standards / security org | Adversarial ML tactic/technique IDs used as optional secondary tags on findings. |
| 58 | NIST AI Risk Management Framework | https://www.nist.gov/itl/ai-risk-management-framework | Standards body | Govern / Map / Measure / Manage framing for AgentLab reports. |
| 59 | NIST AI 600-1: Generative AI Profile | https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf | Standards body | GenAI-specific risk list (e.g. information security, confabulation) for mapping report sections. |
| 60 | Not what you've signed up for: Indirect Prompt Injection (Greshake et al., 2023) | https://arxiv.org/abs/2302.12173 | Academic paper | The foundational indirect-injection threat model: payloads in retrieved web pages, documents and tool output. Basis of AgentLab's injected-content fixtures. |
| 61 | AgentDojo (Debenedetti et al., 2024) | https://arxiv.org/abs/2406.13352 | Academic paper (code: ethz-spylab/agentdojo, MIT; last push 2026-06-02; PyPI 0.1.35, 2025-10-27) | Measuring utility and attack success together: a defense that breaks the task is not counted as a win. |
| 62 | InjecAgent (Zhan et al., 2024) | https://arxiv.org/abs/2403.02691 | Academic paper (code: uiuc-kang-lab/InjecAgent, MIT; last push 2024-07-02, effectively unmaintained) | Attacker-instruction taxonomy (direct harm vs. data exfiltration) for tool-integrated agents. |
| 63 | Identifying the Risks of LM Agents with an LM-Emulated Sandbox (ToolEmu, Ruan et al., 2023) | https://arxiv.org/abs/2309.15817 | Academic paper | Emulated tools plus a safety evaluator, used for testing risky tool use without real side effects. |
| 64 | Defending Against Indirect Prompt Injection Attacks With Spotlighting (Hines et al., Microsoft, 2024) | https://arxiv.org/abs/2403.14720 | Academic paper | Delimiting, datamarking and encoding untrusted content. AgentLab tests whether the agent under test applies such provenance marking and measures the effect. |
| 65 | Defeating Prompt Injections by Design (CaMeL, Google DeepMind, 2025) | https://arxiv.org/abs/2503.18813 | Academic paper (reference code: google-research/camel-prompt-injection, Apache-2.0; last push 2025-06-20, research artifact) | Separating control flow from data flow, with capability/taint tracking on values. Informs AgentLab's data-flow (source to sink) checks. |
| 66 | Design Patterns for Securing LLM Agents against Prompt Injections (Beurer-Kellner et al., 2025) | https://arxiv.org/abs/2506.08837 | Academic paper | Named patterns (action-selector, plan-then-execute, dual LLM, map-reduce, context minimization) used as labels in the defense-coverage report. |
| 67 | The lethal trifecta for AI agents (Simon Willison, 2025) | https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/ | Community | Private data plus untrusted content plus an exfiltration channel. AgentLab flags configurations that combine all three. See also the [prompt-injection tag](https://simonwillison.net/tags/prompt-injection/) and the [Dual LLM pattern](https://simonwillison.net/2023/Apr/25/dual-llm-pattern/). |
| 68 | garak: LLM vulnerability scanner (NVIDIA) | https://github.com/NVIDIA/garak | Open-source project (Apache-2.0; last push 2026-10-02; PyPI 0.17.0, 2026-09-09; active) | Probe / detector / harness architecture for model-level scanning. Informs how AgentLab separates attack generators from success detectors. |
| 69 | PyRIT: Python Risk Identification Tool (Microsoft) | https://github.com/microsoft/PyRIT | Open-source project (MIT; moved from Azure/PyRIT, now archived, to microsoft/PyRIT; last push 2026-10-05; PyPI 1.1.0, 2026-09-04; active) | Orchestrators, converters (encoding/obfuscation transforms) and multi-turn attack strategies. Informs AgentLab's attack-mutation layer. |
| 70 | promptfoo: Red teaming and plugins | https://www.promptfoo.dev/docs/red-team/plugins/ | Open-source project (promptfoo/promptfoo, MIT; last push 2026-10-05; npm 0.123.1, 2026-09-18; active) | Plugin (what to test) vs. strategy (how to deliver) separation, and the [indirect prompt injection plugin](https://www.promptfoo.dev/docs/red-team/plugins/indirect-prompt-injection/) design. Overview: https://www.promptfoo.dev/docs/red-team/ |
| 71 | Anthropic: Mitigating prompt injections in browser use | https://www.anthropic.com/research/prompt-injection-defenses | Official docs (vendor research) | Attack success rate as the headline metric for browser agents under adversarial pages. Informs Playwright-based injection scenarios. |

## 6. Model Context Protocol (MCP security tests)

| # | Title | URL | Source type | What AgentLab took from it |
|---|---|---|---|---|
| 72 | MCP Specification (latest, revision 2026-07-28) | https://modelcontextprotocol.io/specification/2026-07-28 | Standards / official spec (repo modelcontextprotocol/modelcontextprotocol, MIT per README; last push 2026-10-05) | Protocol messages, the [tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools) and [authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization) sections, and the [changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog). The previous revision (https://modelcontextprotocol.io/specification/2025-11-25) is kept for compatibility testing. |
| 73 | MCP Security Best Practices | https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices | Official docs | Confused deputy, token passthrough, SSRF, session hijacking and local-server compromise. Each becomes an MCP security test case. |
| 74 | MCP Python SDK | https://github.com/modelcontextprotocol/python-sdk | Open-source project (MIT; last push 2026-10-05; PyPI `mcp` 2.3.0, 2026-10-02; active) | Client/server primitives used to build AgentLab's deliberately malicious test MCP servers and its MCP client probes. A runtime dependency, not copied code. |
| 75 | Invariant Labs: MCP Security Notification: Tool Poisoning Attacks | https://invariantlabs.ai/blog/mcp-security-notification-tool-poisoning-attacks | Community / security research (vendor) | Hidden instructions in tool descriptions, tool shadowing and rug pulls. These are core MCP test fixtures. See also [GitHub MCP exploited](https://invariantlabs.ai/blog/mcp-github-vulnerability). |
| 76 | MCP-Scan / Snyk agent-scan | https://github.com/snyk/agent-scan | Open-source project (Apache-2.0; originated as Invariant Labs mcp-scan, see https://invariantlabs.ai/blog/introducing-mcp-scan; last push 2026-10-05; active) | Tool-description scanning and tool pinning by hashing. AgentLab records tool-description hashes per run to detect rug pulls. |
| 77 | MCP Safety Audit: LLMs with MCP Allow Major Security Exploits (Radosevich & Halloran, 2025) | https://arxiv.org/abs/2504.03767 | Academic paper | Concrete exploit classes (credential theft, remote access, malicious code execution) via MCP tools, used for test-case coverage. |
| 78 | Model Context Protocol has prompt injection security problems (Simon Willison, 2025) | https://simonwillison.net/2025/Apr/9/mcp-prompt-injection/ | Community | Plain-language threat summary used in AgentLab's MCP report text. |

## 7. Sandboxing (execution sandbox for agents and tools under test)

| # | Title | URL | Source type | What AgentLab took from it |
|---|---|---|---|---|
| 79 | Docker: Resource constraints | https://docs.docker.com/engine/containers/resource_constraints/ | Official docs | `--memory`, `--cpus` and `--pids-limit` defaults for sandboxed runs. |
| 80 | Docker: `docker container run` reference | https://docs.docker.com/reference/cli/docker/container/run/ | Official docs | `--cap-drop=ALL`, `--read-only`, `--tmpfs`, `--network none`, `--security-opt no-new-privileges`, `--user`: the hardened default run profile. |
| 81 | Docker Engine security | https://docs.docker.com/engine/security/ | Official docs | Kernel namespaces, cgroups and capabilities background, plus [seccomp profiles](https://docs.docker.com/engine/security/seccomp/) and [rootless mode](https://docs.docker.com/engine/security/rootless/). |
| 82 | OWASP Docker Security Cheat Sheet | https://cheatsheetseries.owasp.org/cheatsheets/Docker_Security_Cheat_Sheet.html | Standards / security org | Checklist used to review the sandbox profile (don't mount the Docker socket, drop capabilities, read-only rootfs, resource limits). |
| 83 | gVisor documentation | https://gvisor.dev/docs/ | Official docs (google/gvisor, Apache-2.0; last push 2026-10-05; active) | User-space kernel (`runsc`) as an optional stronger isolation runtime. See the [security model](https://gvisor.dev/docs/architecture_guide/security/). |
| 84 | Firecracker | https://firecracker-microvm.github.io/ | Open-source project (firecracker-microvm/firecracker, Apache-2.0; last push 2026-10-05; active) | MicroVM isolation as the high-isolation tier for untrusted code execution. |
| 85 | Anthropic: Making Claude Code more secure and autonomous with sandboxing | https://www.anthropic.com/engineering/claude-code-sandboxing | Official docs (vendor engineering blog) | Combining filesystem and network isolation for agent sandboxes. Informs the default egress-deny posture. |

## 8. Playwright (browser engine)

All Playwright entries are official docs for microsoft/playwright-python (Apache-2.0; last push 2026-10-05; PyPI `playwright` 1.63.0, 2026-09-15; active).

| # | Title | URL | Source type | What AgentLab took from it |
|---|---|---|---|---|
| 86 | Playwright: Trace viewer | https://playwright.dev/python/docs/trace-viewer | Official docs | Recording `context.tracing` per scenario and attaching the trace zip as run evidence. |
| 87 | Playwright: Browser contexts (isolation) | https://playwright.dev/python/docs/browser-contexts | Official docs | One fresh `BrowserContext` per test for cookie/storage isolation between scenarios. |
| 88 | Playwright: Authentication (`storageState`) | https://playwright.dev/python/docs/auth | Official docs | Reusing authenticated state via `storage_state`, treated as a secret and never logged in traces or reports. |
| 89 | Playwright: Locators (role-based) | https://playwright.dev/python/docs/locators | Official docs | Preferring `get_by_role` and other accessibility-based locators for robust browser assertions. |
| 90 | Playwright: ARIA snapshots | https://playwright.dev/python/docs/aria-snapshots | Official docs | Accessibility-tree snapshots as a compact, text-only page representation for judges and assertions. |
| 91 | Playwright: `Page` API (incl. `page.pdf`) | https://playwright.dev/python/docs/api/class-page | Official docs | `page.pdf()` (Chromium only) for exporting HTML reports to PDF, plus screenshot APIs for evidence. |

## 9. Agent skills format

| # | Title | URL | Source type | What AgentLab took from it |
|---|---|---|---|---|
| 92 | Anthropic: Agent Skills overview | https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview | Official docs | `SKILL.md` with YAML frontmatter (`name`, `description`) and progressive disclosure (metadata, then body, then bundled files). Basis of AgentLab's skill format. |
| 93 | Anthropic: Equipping agents for the real world with Agent Skills | https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills | Official docs (vendor engineering blog) | Design rationale, including the warning to install skills only from trusted sources and to audit bundled scripts. |
| 94 | Agent Skills specification (agentskills.io) | https://agentskills.io/specification | Open standard / spec (repo agentskills/agentskills, Apache-2.0; last push 2026-08-09) | Cross-vendor frontmatter fields and directory layout. AgentLab's skill validator checks conformance to it. |
| 95 | anthropics/skills (example skills) | https://github.com/anthropics/skills | Open-source project (mixed: most skills Apache-2.0; `docx`/`pdf`/`pptx`/`xlsx` are **source-available, not open source**; last push 2026-10-05; active) | Structural examples only. Safe to adapt: Apache-2.0 skills, with attribution and NOTICE kept. **Not** safe to adapt: the source-available document skills. AgentLab ships no content from this repo. |

## 10. Observability: OpenTelemetry GenAI semantic conventions (tracing)

| # | Title | URL | Source type | What AgentLab took from it |
|---|---|---|---|---|
| 96 | OpenTelemetry: Semantic conventions for generative AI systems | https://opentelemetry.io/docs/specs/semconv/gen-ai/ | Standards body (CNCF / OpenTelemetry; repo open-telemetry/semantic-conventions, Apache-2.0; last push 2026-10-05; conventions still marked "Development") | Provider-neutral `gen_ai.*` attribute names (`gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.usage.*`) for AgentLab trace spans. |
| 97 | OpenTelemetry: GenAI spans | https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/ | Standards body | Span naming and attributes for model calls. |
| 98 | OpenTelemetry: GenAI agent spans | https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/ | Standards body | `create_agent` / `invoke_agent` / `execute_tool` span types that AgentLab maps trajectory steps onto. |

---

## Dropped during verification

These candidates were checked on 2026-10-05 and left out:

- `https://docs.ollama.com/api/show`: HTTP 404. Replaced by `/api-reference/show-model-details` (entry 20).
- `https://openrouter.ai/docs/features/tool-calling`: HTTP 404 at the old path. Replaced by the current guide path (entry 9).
- `https://playwright.dev/python/docs/accessibility-testing`: HTTP 404 for the Python docs. Not included.
- `https://atlas.mitre.org/matrices/ATLAS`: HTTP 404. The ATLAS home page is used instead.
- `https://platform.openai.com/docs/api-reference/chat`: HTTP 403 from the verification proxy. Replaced by the current `developers.openai.com` reference.
- Microsoft MSRC blog on indirect prompt-injection defenses: HTTP 403, could not be confirmed. Not included.
- BFCL OpenReview paper page: redirected to a bot challenge, could not be confirmed. The leaderboard and blog are cited instead.
- `github.com/invariantlabs-ai/mcp-scan`: no longer found under that name. The project is now `snyk/agent-scan` (entry 76).
- `github.com/Azure/PyRIT`: archived. Replaced by `microsoft/PyRIT` (entry 69).

---

## How AgentLab treats third-party material

- **Informed by, not copied from.** The sources above shaped AgentLab's interfaces, metrics and test design. No third-party source code, prompts, datasets or skill files were copied into the repository. Metrics such as faithfulness and context precision are implemented independently from their published definitions.
- **Third-party skills and docs are untrusted input.** Any external `SKILL.md`, prompt pack, MCP tool description or documentation page that AgentLab reads is treated like untrusted content in a prompt-injection threat model. It is never executed or trusted as instructions as-is. Bundled scripts are not run.
- **Adapted into AgentLab's own controlled skill format.** When an external skill or guide is useful, its ideas are rewritten into AgentLab's own skill format, which is validated against the Agent Skills spec and reviewed. The result carries its own frontmatter, has no network or shell access unless explicitly declared, and is versioned in this repository.
- **Licenses are checked before adapting anything.** Permissive licenses (MIT, Apache-2.0) may be adapted, keeping required attribution and NOTICE text. Source-available or restrictive licenses (for example Elastic License 2.0, or the source-available Anthropic document skills) and repositories with no detected license (for example Accenture/mcp-bench) are design references only. Their content is not adapted.
- **Re-verification.** Maintenance status and versions in this file were observed on 2026-10-05. Re-check them before relying on any project as a dependency.
