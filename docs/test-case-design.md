# Test case design

Before anything is sent to the agent, the **TestDesignerAgent** turns what discovery learned into a **plan**: the
list of tests, and for each one why it exists, how risky it is, whether it will be allowed and able to run, and what it
is expected to cost. The plan is deterministic (the same target and settings give the same plan), is stored with the
run, and can be read before it runs.

```console
$ agentlab test --mock success --suite functional --plan-only --plan-detail --no-probe
```

`--plan-only` designs and shows the plan and runs no test: it sends the target only harmless discovery probes and one
reachability check per interface (an `OPTIONS` or `HEAD` request; the plan says so when one cannot be reached);
`--no-probe` leaves the probe questions out. `--plan-detail` lists every test; `--plan-output plan.md` (or `.json`) writes it to a file; `--confirm` shows it and
asks before running; `agentlab runs plan RUN` shows the plan a finished run used.

## The taxonomy

Tests are organised under 17 areas, **A to Q**, so a plan can say what is covered and what is not. A letter that
cannot apply to the target is *not applicable*, not a gap.

| | Area | Relevant when the target shows |
|---|---|---|
| A | Basic functional | always |
| B | Conversational | always |
| C | Memory | a memory capability |
| D | RAG | RAG or research behaviour |
| E | Tool and function calling | tools, function calling, ReAct or MCP |
| F | Planning | planning, autonomous, ReAct, workflow or supervisor behaviour |
| G | Autonomous agents | autonomous, long-running, computer-use or event-driven behaviour |
| H | Multi-agent systems | multi-agent, supervisor or sub-agents |
| I | MCP | an MCP server |
| J | Browser and UI | a browser agent, computer use or a web interface |
| K | Coding agents | a coding agent or a repository |
| L | Document agents | documents |
| M | Multimodal | a multimodal capability |
| N | Safety and security | always (see below) |
| O | Reliability | always |
| P | Performance | always |
| Q | Cost | always |

### The 28 security categories (N1 to N28)

Area N is split into 28 categories. Every security test counts towards one or more of them, and the plan reports each
as covered, partly covered (some of its tests are predicted blocked), not covered (with the reason) or not applicable.
Tests are non-destructive and use **canaries**: synthetic secrets planted by AgentLab (or by you, in
`known_canaries`) whose appearance anywhere outside the channel they were planted in is a leak
([security.md](security.md#canaries)).

| | Category | Built-in skills that generate its tests |
|---|---|---|
| N1 | Direct prompt injection | `prompt-injection-testing` |
| N2 | Indirect prompt injection | `indirect-prompt-injection-testing` |
| N3 | System instruction override | `prompt-injection-testing`, `data-exfiltration-testing` |
| N4 | Tool injection | `tool-abuse-testing`, `mcp-testing` |
| N5 | Tool output poisoning | `indirect-prompt-injection-testing` |
| N6 | RAG document injection | `indirect-prompt-injection-testing`, `rag-testing`, `document-agent-testing` |
| N7 | Browser content injection | `browser-agent-testing` |
| N8 | Memory poisoning | `memory-testing` |
| N9 | Goal hijacking | `prompt-injection-testing`, `multi-agent-testing` |
| N10 | Excessive agency | `excessive-agency-testing`, `autonomous-agent-testing` |
| N11 | Unauthorized tool usage | `authorization-testing` |
| N12 | Privilege escalation | `authorization-testing` |
| N13 | Data exfiltration | `data-exfiltration-testing`, `indirect-prompt-injection-testing` |
| N14 | Cross-user data leakage | `memory-testing` |
| N15 | Cross-session leakage | `memory-testing` |
| N16 | Secret leakage | `data-exfiltration-testing` |
| N17 | Credential exposure | `data-exfiltration-testing`, `coding-agent-testing` |
| N18 | Unsafe destructive action | `excessive-agency-testing`, `browser-agent-testing`, `coding-agent-testing` |
| N19 | Missing confirmation | `excessive-agency-testing` |
| N20 | SSRF-like unsafe network behaviour | `tool-abuse-testing`, `mcp-testing` |
| N21 | Path traversal | `tool-abuse-testing`, `mcp-testing` |
| N22 | Command-execution safety | `tool-abuse-testing`, `mcp-testing` |
| N23 | Unsafe generated-code execution | `coding-agent-testing` |
| N24 | Denial-of-wallet and runaway loops | `cost-testing`, `autonomous-agent-testing`, `browser-agent-testing`, `multi-agent-testing` |
| N25 | Recursive agent delegation | `multi-agent-testing` |
| N26 | Malicious external content | `indirect-prompt-injection-testing`, `browser-agent-testing`, `multimodal-agent-testing` |
| N27 | Malicious skill or tool instructions | `mcp-testing` |
| N28 | Insecure fallback behaviour | `authorization-testing` |

A category is credited from the test ids a skill produces, and a few need something the target must offer: N4, N20,
N21 and N22 need a tool that takes a path, URL, command or query; N5 needs an interface that can replace a tool result;
N8, N14 and N15 need a memory capability. When it is missing the category shows as not covered or not applicable with
that reason instead of disappearing.

## How a plan is made

1. **Skills are matched** against the target's profile ([skills.md](skills.md#how-a-skill-is-chosen)) and every
   decision is recorded with its reason.
2. **Each selected skill generates tests** from the profile: the tools found, the documents given, the interfaces
   available and the intensity. A skill that fails to generate is reported (`skill_failed`) and contributes nothing;
   it does not stop the plan.
3. **Tests outside the suite are dropped**, and **duplicates** (the same inputs, planted context and checks under
   another name) are removed with a note.
4. **Your own tests** ([below](#writing-your-own-tests)) are added; they are never trimmed.
5. **Every test is classified** safe, controlled or high impact and put through the
   [authorization gate](security.md#the-authorization-gate) and the environment checks. A test that cannot run is
   marked **predicted BLOCKED** with the reason: a missing interface, credential, judge, browser or Docker, a risk class the
   target's owner has not authorised, or a capability the interface lacks (it cannot plant a document, say).
6. **The plan is fitted to its budget** ([below](#limits-and-trimming)) and **coverage** of A to Q and N1 to N28 is
   computed from what remains.
7. **Warnings** are added: `empty_plan` and `no_interface` (blockers), `many_blocked` (a quarter or more of the tests),
   `budget_exceeded`, `coverage_gap`, `plan_trimmed`, `duplicate_test`, `skill_problem`, and similar.

A plan carries, for each test: the skill and version, the taxonomy letters and security categories it counts for, the
reasons and the evidence in the profile behind them, its risk class and the gate's reasons, whether it is runnable or
predicted blocked (and why), whether it is selected, how many calls, tokens and seconds it is expected to take, and its
origin (`skill`, `user`, `llm` or `adaptive`). For the whole plan: the skills considered, the coverage tables, a budget
estimate, the assumptions it was built on, and a hash of its content that decides whether two runs can be compared
([reports.md](reports.md#comparing-runs)).

The numbers in the budget estimate are **planning estimates**, not measurements: they come from
`planning.seconds_per_call`, `planning.tokens_per_call` and `planning.judge_tokens_per_call`
([configuration.md](configuration.md#planning)), and a cost is shown only when the model's price is known
(`providers[].pricing`); otherwise the plan says it is unknown.

## Suites and intensity

A **suite** says what the run is about; an **intensity** says how deep each skill goes.

| Suite | Contains |
|---|---|
| `discovery` | A few cheap checks (areas A and B, at most three tests per skill) that show whether the target works at all. |
| `functional` | Behaviour and quality of every capability found; everything except N. |
| `security` | Safety and security only (N). |
| `browser` | Browser and UI behaviour (J). |
| `reliability` | Repeatability, latency and cost (O, P and Q). |
| `full` | Everything that applies to the target (the default). |
| `regression` | The tests of an earlier plan, re-run unchanged (`--baseline RUN_ID`). |

`--intensity quick|standard|thorough` (default `standard`) changes how many items of each family a skill generates.
For the built-in mock agent the `full` suite plans 42, 56 and 58 tests at the three intensities. Areas outside the
chosen suite are shown as *not applicable (outside the suite)*, so a `functional` run does not look like a security
review that found nothing.

## Limits and trimming

* A skill adds at most `evaluation.max_tests_per_skill` tests (default 40); the rest stay in the plan, marked
  deselected with that reason.
* `planning.max_tests` (default 400, `--max-tests`) and 90 % of `limits.max_tokens` bound the whole plan. The limit is
  **soft**: when the plan is too large, every taxonomy area and every security category keeps its most important
  runnable test, and every test you wrote stays; the others are dropped in order of priority until the plan fits, so
  a plan can still exceed `max_tests`. Trimmed tests are **not deleted**: they remain in the plan, marked deselected
  with the reason, and a `plan_trimmed` warning says how many.
* Repetitions multiply cost: `evaluation.repetitions` (`--repetitions`), `evaluation.repetitions_by_risk`, and
  `reliability_repetitions` for reliability tests. The estimate includes them.

## Writing your own tests

Business-critical scenarios are better written by the owner of the agent than guessed by AgentLab. Put them in a YAML
or JSON file (a list, or `tests:` holding a list) and pass `--tests FILE` (repeatable):

```yaml
tests:
  - name: Refund window is quoted correctly
    input: How many days do I have to return an item?
    must_contain: ["30 days"]
    must_not_contain: ["60 days"]
    severity_on_failure: high

  - name: Order lookup asks for the order number first
    turns:
      - input: Where is my order?
        assertions:
          - {type: regex, params: {pattern: "order (number|id)"}}
      - input: It is 12345
        assertions:
          - {type: not_empty}
    severity_on_failure: medium
    tags: [orders]
```

Run with `agentlab test --mock success --tests my-tests.yaml --skills agent-fingerprinting --suite functional`
(`--skills agent-fingerprinting` adds no tests of its own), this plans two tests, `USER-REFUND-WINDOW-IS-QUOTED-001`
and `USER-ORDER-LOOKUP-ASKS-FOR-TH-002`, both safe, origin `user`, and both fail against the mock agent, which knows
nothing about your refund policy.

* Every field of a test ([below](#the-fields-of-a-test)) is accepted. Three shorthands make the common checks short:
  `must_contain`, `must_not_contain` (case-insensitive) and `must_match` (regular expressions).
* A test with no checks gets `not_empty`, unless it has `judge` criteria.
* Defaults: category `functional` (area A), id `USER-<NAME>-<NNN>`, objective = the name, tag `user-defined`.
* Nothing in the file is executed. An assertion `type` must be one of the registered kinds
  ([evaluation.md](evaluation.md#deterministic-assertions)). A test that is invalid (wrong shape, unknown kind, no input,
  duplicate id) is **reported and skipped**; it does not abort the plan. Files are limited to 1 MB and 500 tests.
* Assertion settings go under `params`: `{type: contains, params: {value: "30"}}`.

## The fields of a test

| Group | Fields |
|---|---|
| Identity | `id`, `name`, `category`, `subcategory`, `objective`, `rationale` (why it was generated), `skill`, `skill_version`, `tags` |
| Risk | `risk_level` (`safe`, `controlled`, `high_impact`), `severity_on_failure` (`critical`, `high`, `medium`, `low`, `info`) |
| What it needs | `preconditions` (`sandbox`, `judge` and `repository_execution` are understood by the gate), `required_credentials` (names of stored credentials), `required_interfaces` (`api`, `web`, `command`, `mcp`, `llm`, `mock`) |
| What it sends | `input` (one turn) or `turns` (each with `input`, `session`, `attachments`, `assertions`), `browser_steps`, `context` (free-form data for the engine, for example a planted canary or a workspace) |
| What is expected | `expected_behavior`, `expected_output`, `expected_tool_calls` (`name`, `arguments`, `match`: `exact`, `subset`, `name_only`), `forbidden_behavior` |
| How it is checked | `assertions` (`type`, `params`, `description`, `severity`, `weight`, `metric`, `required`, `turn`), `judge` (`metric`, `rubric`, `weight`, `threshold`), `evaluation_metrics` |
| Budget | `timeout` (60 s), `max_steps` (20), `max_cost` ($0.50), `max_tokens` (20 000), `repetitions` |
| Other | `isolation_key` (tests that share a key never run in parallel), `score_category`, `status`; and three notes for people that are recorded with the test and change nothing about how it runs: `cleanup_strategy`, `evidence_requirements`, `applicable_agent_types` |

The effective budget of an attempt is the smaller of the test's own and the run's limits (`limits.max_test_cost_usd`,
`limits.max_tokens`, `limits.max_steps`, `evaluation.timeout_seconds`); see
[configuration.md](configuration.md#limits).

Strings in a test may contain **placeholders** that are resolved when the attempt runs: `{{canary:NAME}}` is a unique
synthetic secret for this run (the same name gives the same value within a run), `{{b64:canary:NAME}}` is its
base64 form, and `{{site_url}}` is the address of the instrumented local site a browser test is pointed at.

## The adaptive second wave

After the first wave AgentLab looks at what it found and decides where to dig deeper. It is deterministic and
**never adds tests where nothing was found**: a clean first wave ends the run and the report says a second wave was not
needed. When there are failures or unstable results, wave 2 adds, within `planning.adaptive_max_tests` (default 60):

* **variants** of every failed single-turn test: the same checks with a different wording, up to
  `planning.adaptive_variants_per_failure` (default 3). Functional tests are retyped with `typos`, `uppercase`,
  `polite` openings, a `warmup_turn` or irregular `spacing`; adversarial tests use `role_play`, `urgency`,
  `warmup_turn`, `persistence`, `typos` and `spacing`. They answer "is the weakness tied to one wording?";
* a **deeper pass** (thorough intensity, duplicates removed) of the skills that produced failures;
* **re-checks** of tests that were unstable, repeated `planning.adaptive_repetitions` times (default 5) so a pass
  rate is measured instead of guessed.

Wave-2 tests are evidence, **not scored**, so a weakness is not counted twice; the report lists them separately.
`--no-second-wave` skips the wave.

## Model-suggested tests

With `evaluation.llm_test_generation: true` and a configured evaluator provider, a model may propose a few extra
**safe** functional scenarios. Off by default. The model is never the target; what it is told about the target is
fenced as untrusted data; its answer is schema-checked and filtered (plain conversational input, bounded size, no
URLs or instruction-like text, safe risk only, severity at most medium, simple must-contain / must-not-contain checks
plus one judged criterion); and the tests are labelled *model-suggested (unverified)* and sit in the plan like any
other, so a person can deselect them before anything runs.

## Re-running and comparing

* `agentlab test --baseline RUN_ID` re-runs the tests of an earlier run unchanged (suite `regression`), so the two
  runs can be compared ([reports.md](reports.md#comparing-runs)).
* `--only TEST_ID` (repeatable) runs just those tests; with `--baseline RUN_ID` it replays the exact test of that run.
* Skills generate tests without randomness: the same target, probe results and options give the same tests, so there is
  no seed to set. (A run used to accept `--seed`; it never changed a plan and was removed rather than left as a dial that
  does nothing.) The exception is model-suggested tests, which a model proposes and which can differ between runs.

## What a plan cannot do

* It cannot know how the agent behaves: that is what the run measures. A plan with 200 tests says what will be asked,
  not what will be found.
* Coverage is credited from the tests a skill produced and the ids they carry, not from a measurement of the agent's
  code: "covered" means *tested*, never *secure*.
* The estimates of time, tokens and cost are assumptions. The run's real usage is recorded, and `limits.*` stop a run
  that exceeds them ([configuration.md](configuration.md#limits)).
