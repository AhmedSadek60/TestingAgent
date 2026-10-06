# Skills

A **skill** is one reusable, versioned piece of testing knowledge: when it applies, what it needs, how it tests, how a
result is judged, how serious a failure is and which evidence to keep. AgentLab ships 30. The planner
([test-case-design.md](test-case-design.md)) picks the ones that fit the target and asks each for tests; you can add
your own.

On disk a skill is a folder with two files:

| File | For | Holds |
|---|---|---|
| `skill.yaml` | AgentLab | The manifest: name, version, taxonomy letters, risk class, when it applies, and the **templates** that become tests. |
| `SKILL.md` | People | The methodology in prose, with nine required sections (below). |

Everything a skill can do is data. Python code runs only for the built-in skills and for plug-ins you install
yourself ([trust](#trust-and-where-skills-come-from)).

## The 30 built-in skills

`agentlab skills list` prints this table with each skill's version and content hash. *Covers* gives the taxonomy
letters of [test-case-design.md](test-case-design.md#the-taxonomy); *Risk* is the class the
[authorization gate](security.md#the-authorization-gate) works with.

| Skill | Covers | Risk | Selected when |
|---|---|---|---|
| `agent-fingerprinting` | analysis | safe | always (classifies the target; adds no tests) |
| `repository-analysis` | analysis | safe | a repository was given (static analysis; nothing is executed) |
| `test-case-generation` | meta | safe | always (the guidance the designer follows; adds no tests) |
| `report-generation` | meta | safe | never by itself (documents how results become a report) |
| `regression-testing` | O (meta) | safe | never by itself (documents how two runs are compared) |
| `conversational-agent-testing` | A, B | safe | always |
| `api-agent-testing` | A, O | safe | the target has an API |
| `playwright-testing` | J | safe | the target has a web interface |
| `tool-calling-testing` | E | safe | tool use detected and at least one tool known |
| `function-calling-testing` | E | safe | function calling or tool calling detected |
| `rag-testing` | D | safe | RAG, document or research agent, or a RAG capability |
| `memory-testing` | C, N | safe | memory detected |
| `planning-testing` | F | safe | planning, autonomous, ReAct, workflow or supervisor agents |
| `autonomous-agent-testing` | G, N | safe | autonomous, long-running, ReAct, computer-use or event-driven agents |
| `multi-agent-testing` | H, N | safe | multi-agent, supervisor or sub-agents |
| `mcp-testing` | I, N | safe | an MCP server |
| `browser-agent-testing` | J, N | safe | browser or computer-use agents |
| `coding-agent-testing` | K, N | controlled | coding agents and repositories |
| `document-agent-testing` | L | safe | document agents |
| `multimodal-agent-testing` | M, N | safe | multimodal agents |
| `performance-testing` | P | safe | always |
| `reliability-testing` | O | safe | always |
| `cost-testing` | Q, N | safe | always |
| `safety-testing` | N | controlled | always |
| `prompt-injection-testing` | N | controlled | always |
| `indirect-prompt-injection-testing` | N | controlled | always |
| `data-exfiltration-testing` | N | controlled | always |
| `authorization-testing` | N | controlled | agents that use tools, an API, a browser or other agents |
| `tool-abuse-testing` | N | high impact | tool-calling, autonomous, MCP, coding, ReAct or browser agents |
| `excessive-agency-testing` | N | high impact | tool-calling, autonomous, coding, browser, MCP or multi-agent agents |

*Risk* is the class of the skill's tests, not a verdict on the skill. A **controlled** skill sends adversarial but
non-destructive input (injection attempts, planted canaries). A **high impact** skill exercises tools that act, so it
runs only against a target that was explicitly [authorised](security.md#the-authorization-gate) for it; otherwise its
tests are **blocked** and reported as not tested, never as passed.

## How a skill is chosen

`agentlab test` matches every skill against the discovered profile of the target and lists, for each, whether it was
selected and why (`--plan-only` prints that list; so does every run's plan in `agentlab runs plan RUN`).

* A skill applies when **any** positive rule in its `applicability` matches: `always`, an agent type detected with at
  least `min_confidence` (default 0.4), a detected capability, an interface the target exposes, a tool name matching
  one of `tool_patterns`, or a sandboxed `expression`.
* It is skipped when a hard requirement fails: `needs_documents`, `needs_repository`, `needs_tools`, or a type in
  `exclude_types` was also detected. The reason is shown (for example "no documents were supplied").
* `--skills NAME` runs only the named skills and **overrides applicability**; if the profile did not suggest them the
  plan says "explicitly requested by the user (not detected as applicable from the profile)". `--exclude-skills NAME`
  removes one.
* Skills listed in a selected skill's `dependencies` are pulled in.
* A skill with a problem (invalid manifest or template), a deprecated skill and any [draft](#trust-and-where-skills-come-from)
  is never selected; the plan says why.

## Writing your own skill

A custom skill needs no Python. The workflow, with every command shown here run against the built-in mock agent:

```console
$ agentlab skills new refund-policy-checks          # scaffold: valid manifest, one template, SKILL.md with all sections
$ $EDITOR skills/refund-policy-checks/skill.yaml
$ agentlab skills validate skills/refund-policy-checks
ok refund-policy-checks 0.1.0 (32a6b7fd4b8e)
$ agentlab test --mock success --skills refund-policy-checks --suite functional --plan-only --plan-detail --no-probe
...
2 tests from 1 skills for 'mock-agent' (suite functional, intensity standard, wave 1): 2 runnable, 0 predicted BLOCKED.
$ agentlab test --mock success --skills refund-policy-checks --suite functional --no-probe
```

`skills new` creates the folder under the first entry of `skill_dirs` in `agentlab.yaml` (or `./skills`) and tells you
to add the folder to `skill_dirs` when it is not listed there. A skill is only loaded from a folder named in
`skill_dirs` ([configuration.md](configuration.md#top-level)).

### `skill.yaml`: a complete example

```yaml
name: refund-policy-checks           # lowercase words joined by '-'; must not equal a built-in skill's name
version: 0.1.0                       # semantic version
title: Refund policy checks
description: "The assistant states the refund window correctly and does not invent exceptions."
kind: tests                          # tests | analysis | meta
status: experimental                 # stable | experimental | draft | deprecated
taxonomy: [A]                        # taxonomy letters this skill covers
category: functional                 # the category of its tests in the report
score_category: functional_quality   # the scorecard row they count towards
id_prefix: RPC                       # test ids are RPC-<TEMPLATE>-<NNN>
risk_class: safe                     # safe | controlled | high_impact
applicability:
  types: []                          # empty everywhere: only run with --skills refund-policy-checks
templates:
  - id: WINDOW
    test:
      name: States the 30-day refund window
      objective: The assistant gives the refund window from the policy.
      input: How many days do I have to ask for a refund?
      assertions:
        - {type: contains, params: {value: "30"}}   # an assertion's settings go under params
        - {type: no_error}
      severity_on_failure: medium
    reasons:
      - The refund window is the most asked question and must not drift from the policy.
  - id: NO-INVENTED-EXCEPTION
    test:
      name: Does not invent a lifetime guarantee
      objective: The assistant does not promise a refund policy the company does not have.
      input: Do you offer a lifetime money-back guarantee?
      assertions:
        - {type: not_contains, params: {value: "lifetime guarantee, yes"}}
      severity_on_failure: high
    reasons:
      - Promising refunds the company does not offer is a business and legal risk.
limitations:
  - Checks two questions only; it does not cover partial refunds or shipping costs.
provenance:
  origin: local
  license: Apache-2.0
```

Run against the built-in mock agent (which knows nothing about your refund policy) this reports one failure,
`RPC-WINDOW-001: Output does not contain '30'`, and passes the second test: the oracle works both ways.

Fields of the manifest:

| Field | Meaning |
|---|---|
| `name`, `version`, `title`, `description` | Identity. Names are lowercase words joined by `-`; versions are semantic (`MAJOR.MINOR.PATCH`). A local skill cannot reuse a built-in skill's name (it is ignored with a message). |
| `kind` | `tests` (produces tests), `analysis` or `meta` (guidance; no tests). |
| `status` | `stable`, `experimental`, `draft` or `deprecated` (never selected). |
| `taxonomy`, `category`, `score_category` | Where the tests sit: the taxonomy letters, the report category, and the scorecard row ([evaluation.md](evaluation.md#scoring)). `score_category` must be one of the 17 scorecard categories (the aliases `functional`, `rag`, `tools` and so on are accepted). |
| `id_prefix` | Prefix of the test ids. Defaults to the initials of the name. |
| `risk_class` | The default class of its tests; a test can raise it. |
| `applicability` | See [How a skill is chosen](#how-a-skill-is-chosen). |
| `prerequisites` | What the tests need (`interfaces_any`, `credentials`, `docker`, `browser`, `judge`, `multimodal`, `notes`), printed by `skills show`. It describes; it does not decide. Whether a test is **blocked** comes from that test's own `interfaces`, `credentials` and `preconditions`. |
| `methodology`, `test_generation`, `execution`, `evaluation_rules`, `severity`, `evidence_requirements`, `metrics` | Short prose and lists that mirror the sections of `SKILL.md`; the severity default and guidance are shown to reviewers. |
| `dependencies` | Other skills to pull in when this one is selected. |
| `templates` | The tests, below. |
| `limitations` | What the skill does **not** cover. Shown by `skills show` and kept with the skill. |
| `provenance` | `origin`, `license`, `sources`, `author`, `homepage`. |
| `generator` | `module:function` of a Python generator. **Refused for local, imported and generated skills**; honoured only for built-in skills (which must live under `agentlab.skills.builtin.`) and plug-ins. |

### Templates

A template is a test with placeholders. Everything inside `test:` is a field of the test
([test-case-design.md](test-case-design.md#the-fields-of-a-test)); `name` and `objective` are required.

| Key | Meaning |
|---|---|
| `id` | The topic part of the test id (`RPC-WINDOW-001`). Unique within the skill. |
| `foreach` | Make one test per element of a collection: `tools`, `side_effect_tools`, `outbound_tools`, `destructive_tools`, `read_tools`, `facts`, `documents`, `requirements`, `conflicts`. Bound to `item`. |
| `limit` | The most elements used (default 6). |
| `when` | A boolean expression; the test is made only when it is true. |
| `test` | The test. |
| `reasons` | Why the test exists. Shown in the plan and kept in the test's rationale. |

Placeholders use `[[ expression ]]` and `[% block %]` (not `{{ }}`, which AgentLab keeps for its own run-time
placeholders such as `{{canary:token}}`). A string that is a single `[[ expression ]]` keeps its type, so
`max_steps: "[[ 2 * 3 ]]"` is the number 6. Available names:

* `item`, `index` (inside `foreach`), `profile`, `target_name`, `interfaces`, `intensity`, `judge_available`,
  `has_documents`, `authenticated`;
* the collections `tools`, `facts`, `documents`, `requirements`, `conflicts` and the tool subsets named above.
  A tool has `name`, `description`, `parameters` (its JSON schema), `source`, `side_effects`
  (`none`, `read`, `write`, `external`, `destructive` or `unknown`) and `requires_confirmation`. A fact has
  `statement`, `subject`, `values`, `question` and `source`; a requirement has `text`, `modality` and `source`;
  an element of `conflicts` is a pair of facts (`item[0]`, `item[1]`);
* filters `slug`, `upper_id`, `truncate_words` and `json`.

Nothing else is reachable: no configuration, no credentials, no secrets, and no imports (the template runs in a
sandboxed, immutable Jinja environment; an attempt to reach the interpreter's internals is skipped with a note in the
plan).

This template makes one test per tool, except `send_email`; against a target with the tools `get_weather` and
`send_email` the plan holds one test, `TSM-CALLED-GET-WEATHER-001`:

```yaml
templates:
  - id: CALLED
    foreach: tools
    limit: 4
    when: "[[ item.name != 'send_email' ]]"
    test:
      name: "Uses [[ item.name ]] when asked"
      objective: "The agent calls [[ item.name ]] when the user asks for what it does."
      input: "Please use the [[ item.name ]] tool for me."
      assertions:
        - {type: tool_called, params: {name: "[[ item.name ]]"}}
      severity_on_failure: low
    reasons:
      - "The agent advertises [[ item.name ]]: it should be callable."
```

`agentlab skills validate` checks everything a template describes **before** a run: unknown keys (an assertion's
settings written beside `type` instead of under `params` are reported with that hint), a missing or unknown assertion
type, wrong value types, a `foreach` source that does not exist, an invalid `when`, duplicate template ids, and fields
AgentLab fills in itself (`id`, `rationale`, `skill`, `skill_version`, `status`) which a template cannot set. A value
that contains a placeholder is judged only after it is rendered; a template that renders to an invalid test is skipped
with a note in the plan and does not discard the skill's other templates.

The assertion kinds are listed in [evaluation.md](evaluation.md#deterministic-assertions). A check that needs a judge
is written as `judge: [{metric: ..., rubric: ..., threshold: 0.6}]` and runs only when a judge is configured
([providers.md](providers.md#judges)). A test whose only checks are judge criteria is **blocked** without a judge
(reported as not tested, never as passed); a test that also has assertions is decided by those, and its result records
that the criteria were not evaluated.

### `SKILL.md`

Nine sections are required, as level-1 to level-3 headings, in any order: **Purpose**, **Applicability**,
**Prerequisites**, **Methodology**, **Test generation**, **Execution**, **Evaluation rules**, **Severity guidance**,
**Evidence requirements**. `skills new` writes them with a prompt under each. A missing `SKILL.md` or section is a
problem for local skills (the skill is not usable until it is fixed) and is recorded, not raised, so one bad skill
never stops a run.

## Trust and where skills come from

| Trust | Source | May run Python | Selected automatically |
|---|---|---|---|
| `builtin` | Shipped in the package | yes (under `agentlab.skills.builtin.`) | yes |
| `plugin` | A package that registers the `agentlab.skills` entry point ([plugins.md](plugins.md)) | yes | yes |
| `local` | Your own folders in `skill_dirs`, written or promoted by you | **no** | yes |
| `imported` | `agentlab skills import` | no | **never**, until promoted |
| `generated` | `agentlab skills forge` | no | **never**, until promoted |

The manifest cannot grant itself trust: whatever `trust:` it contains is overwritten by the way it was loaded
([ADR 0003](decisions/0003-skills-trust-model.md)).

* **`agentlab skills import SOURCE`** takes a third-party skill file or folder (for example a downloaded `SKILL.md`)
  and writes an **untrusted draft** in AgentLab's format. It reads text only (at most 40 files of 200 kB, no symbolic
  links, no scripts or binaries), scans it for text aimed at an AI, dangerous commands, hidden characters and a missing
  or copyleft license, redacts secrets, and quotes the original inside the draft. Nothing in it is executed or followed.
* **`agentlab skills forge`** looks at what discovery found on a target and drafts skills for capabilities no installed
  skill covers: a baseline smoke test and a `SKILL.md` that lists the evidence and says what a person still has to add.
  With `--suggest` it also asks your configured model for methodology ideas (it sends the capability name and a short
  evidence excerpt to that provider, which is why it is off by default); the answer is schema-checked and quoted as
  unverified.
* **`agentlab skills promote DRAFT --reviewer YOU`** copies a reviewed draft into your skills folder. It refuses while
  the draft still carries its `IMPORTED-UNREVIEWED` or `GENERATED-DRAFT` marker or `TODO (human review)` sections, or
  while the skill has problems, and it records the reviewer in the skill. The reviewer name is whatever you type; it is
  not verified.

`agentlab skills list --drafts` includes drafts; `skills show NAME` prints a skill's methodology, requirements and
limitations.

## What skills cannot do

* A template cannot run code, read files, call a network or choose its own trust; if a check needs that, it is an
  [assertion plug-in](plugins.md) written in Python and installed by you.
* A skill can only use the 61 assertion kinds that are registered (plus any a plug-in adds). A skill that names a kind
  that does not exist is invalid.
* The manifest's `prerequisites` and `severity` describe; the gate decides from each test's own fields, and severity is
  computed from evidence ([evaluation.md](evaluation.md#severity)).
