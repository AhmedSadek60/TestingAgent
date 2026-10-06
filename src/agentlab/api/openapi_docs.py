"""The words of the API reference: what every parameter and every field of every request and response means.

Most models carry their own descriptions (``Field(description=...)``). The domain models that are shared with the command line
and the reports (a test case, a result, a profile, a plan, a comparison) are documented here instead, so that describing the
API does not mean editing the engine. A description in the code always wins; this fills in only what is missing, and a test
fails when anything reachable from the API is left undescribed or when a line here names something that does not exist."""

from __future__ import annotations

import re
from typing import Any

# ============================================================================================== parameters
PARAMETERS: dict[str, str] = {
    "project": "Project name or id",
    "target_id": "Id of a stored target",
    "plan_id": "Id of a test plan (the run id returned by `POST /test-plans`)",
    "run_id": "Id of a run, or any unambiguous prefix of it",
    "test_id": "Id of a test (for example `PI-DIRECT-001`); for results a result id also works",
    "trace_id": "Id of a trace, from `GET /test-runs/{run_id}/traces`",
    "report_id": "Id of a report version",
    "artifact_id": "Id of a stored artifact (`sha256-<hex>`)",
    "name": "Name",
    "format": "Report format: json, md, html or pdf",
    "status": "Only items with this status",
    "kind": "Only items of this kind",
    "category": "Only tests of this category",
    "severity": "Only items of this severity: info, low, medium, high or critical",
    "security": "true: only security findings; false: only the others",
    "limit": "Largest number of items to return",
    "offset": "Number of items to skip",
    "after": "Continue after this cursor: the `X-Next-Cursor` of an earlier response, or the `id` of the last event received",
    "last-event-id": "Resume after this event id (sent by browsers when they reconnect)",
    "run_a": "The baseline run of the comparison (an id or prefix)",
    "run_b": "The run being compared with the baseline (an id or prefix)",
    "provider": "Only this provider",
    "live": "Also contact remote providers with their keys (default: only check that keys are set)",
}
PATH_PARAMETERS: dict[tuple[str, str], str] = {
    ("/credentials/{name}", "name"): "Name of the stored credential",
    ("/credentials/{name}/rotate", "name"): "Name of the stored credential",
    ("/providers/{name}/check", "name"): "Name of a configured provider",
    ("/skills/{name}", "name"): "Name of a skill, from `GET /skills`",
    ("/test-runs", "kind"): "Only plans (`plan`) or only runs (`run`)",
    (
        "/test-runs",
        "status",
    ): "Only runs with this status (pending, running, completed, failed, cancelled, stopped_due_to_*)",
    ("/test-runs", "target"): "Only runs of the target with this id",
    (
        "/test-runs/{run_id}/results",
        "status",
    ): "Only results with this status (passed, failed, blocked, error, timeout, skipped)",
    ("/test-runs/{run_id}/events", "test_id"): "Only events of this test",
    ("/test-runs/{run_id}/traces", "test_id"): "Only traces of this test",
    ("/test-runs/{run_id}/artifacts", "kind"): "Only artifacts of this kind (plan, trace, screenshot, report, ...)",
    ("/test-runs/{run_id}/findings", "status"): "open, confirmed or false_positive",
}

# ================================================================================================== schemas
# Field names that mean the same wherever they appear.
COMMON = """
id: Unique identifier
name: Name
description: What it is, in words
summary: A short statement of the whole
created_at: When it was created (UTC)
updated_at: When it was last changed (UTC)
started_at: When it started (UTC)
finished_at: When it ended (UTC); empty while it is in progress
timestamp: When it happened (UTC)
run_id: Id of the run it belongs to
test_id: Id of the test it belongs to
project_id: Id of the project it belongs to
target_id: Id of the target it belongs to
size: Size in bytes
media_type: Media (MIME) type
sha256: SHA-256 of the content, which is also its identity
url: Where it can be reached
warnings: Things the reader should know that did not stop the work
error: What went wrong, when something did
note: A remark that qualifies the entry
notes: Remarks that qualify the whole
passed: Whether it passed
score: Score from 0 to 1 (0 to 100 where the field says so)
confidence: How sure the conclusion is, from 0 to 1
severity: How serious a failure is: info, low, medium, high or critical
category: The test category (the group of tests it belongs to)
version: Version
provider: Name of the configured provider
model: Name of the model
tokens: Tokens used
cost_usd: Cost in US dollars, from the provider's declared prices (0 when unknown or free)
latency_ms: Time taken, in milliseconds
weight: How much it counts relative to its siblings
message: What it says, in words
level: How serious or how important
reason: Why
label: Display name
delta: Change from the first run to the second (second minus first)
ok: Whether everything checked is in order
timeout_seconds: Longest wait, in seconds
headers: HTTP headers to send. Never put a secret here: store it with `POST /credentials` and name it in `auth_credential`
auth_credential: Name of a stored credential (`POST /credentials`) to use for authenticated requests
type: The kind
status: Where it stands
kind: The kind
details: Further detail
tags: Free labels
"""

SCHEMAS = """
[AgentProfile]
target_name: Name of the target this profile describes
summary: What the target appears to be, in a few sentences
modes: The evaluation modes that apply (black box, white box, ...)
types: The kinds of agent the target looks like, each with a confidence and the evidence for it
interfaces: How AgentLab can talk to it: api, web, command, mcp, llm, mock
authentication: What was learned about how the target authenticates callers
tools: The tools the target can call, with what each is known to do
data_sources: Where the target gets its data from
memory: What was learned about conversational or long-term memory
rag: What was learned about retrieval (vector stores, knowledge bases)
browser: What was learned about browser or GUI use
multi_agent: What was learned about other agents it delegates to
mcp: What was learned about MCP servers it uses
models: The language models it appears to use
frameworks: Agent frameworks and libraries it appears to use
languages: Programming languages in its repository, by number of files
expected_workflows: The tasks the target is meant to do
limitations: What could not be learned or tested, and why
attack_surfaces: The places where the target accepts untrusted input
capability_matrix: Each capability, whether it was detected and whether AgentLab can test it
architecture: A graph of the target's parts and how they connect
strategy: The testing strategy the profile suggests
knowledge_items: How many facts were extracted from the supplied documents
documents: The documents the profile was built from
repository: What was learned from the repository (commit, files, entry points)
raw_signals: The signals the fingerprint was computed from, kept so it can be audited

[ApiConfig]
url: Address of the agent's endpoint
method: HTTP method to use
protocol: How to talk to it: rest, sse, websocket or graphql
request_template: JSON body sent for each message; `{{input}}`, `{{session_id}}` and `{{attachments}}` are substituted
response: Where to find the answer in the response
session_header: Header that carries the conversation id, when it is not part of the body
graphql_query: Query to send when the protocol is graphql
openapi_url: Where the agent's OpenAPI document is, when it has one

[ArchEdge]
source: Id of the node the edge starts at
target: Id of the node the edge ends at
label: What the connection is

[ArchNode]
id: Id of the node
label: Display name
kind: What kind of part it is (agent, tool, data store, model, ...)

[ArchitectureGraph]
nodes: The parts
edges: How the parts connect

[ArtifactOut]
id: Artifact id (`sha256-<hex>`); the content decides it, so identical files are stored once
kind: What the artifact is (plan, trace, screenshot, report, analysis, ...)
sensitivity: `restricted` for evidence that may show a signed-in session; it is served only on request
name: File name it was stored under
test_id: Id of the test that produced it, if one did
url: Where to download it

[AssertionResult]
type: Which check was evaluated
passed: Whether the check held
score: Score of the check from 0 to 1
message: What was checked and what was found
metric: The quality dimension the check measures
weight: How much the check counts within its test
evidence: The values the check looked at
turn_index: The conversation turn it applied to, when it applied to one
severity: How serious a failure of this check is
required: Whether failing it fails the whole test
evaluator_error: The check itself could not be evaluated; this says nothing about the target

[AssertionSpec]
type: Which check to run (the name of an evaluator)
params: Parameters of the check
description: What the check is for
severity: How serious a failure of this check is
weight: How much the check counts within its test
metric: The quality dimension the check measures
required: Whether failing it fails the whole test
turn: Apply to this turn only (0-based); default: the last turn

[AttemptResult]
attempt: Number of the repetition, from 1
status: Outcome of this attempt
assertions: The deterministic checks and their outcomes
judge: What the LLM judges decided, where a judge was used
trajectory: What the agent did: its replies, tool calls, retrievals and steps
latency_ms: Time the attempt took
tokens: Tokens the target used, when it reports them
cost_usd: Cost of the attempt, when known
steps: Number of steps the agent took
error: Why the attempt could not be completed, if it could not
error_kind: Whether the target, the infrastructure or the policy caused the error
outputs: What the agent answered
trace_id: Id of the trace that recorded this attempt

[BrowserStep]
action: What to do in the browser
target: CSS selector, URL or text the action applies to
value: Text to type, key to press or value to expect
role: Accessible role to find the element by
name: Accessible name to find the element by
timeout_ms: How long to wait for the step, in milliseconds

[BudgetEstimate]
tests: Tests the plan would run
attempts: Attempts, counting repetitions
target_calls: Requests that would be sent to the target
judge_calls: Calls that would be made to LLM judges
est_tokens: Estimated tokens
est_cost_usd: Estimated cost in US dollars; empty when prices are unknown
cost_note: How the cost estimate was made, or why there is none
serial_seconds: Estimated time if the tests ran one after another
est_wall_seconds: Estimated time with the configured parallelism
within_limits: Whether the plan fits inside the configured cost and time limits
notes: How the estimate was made

[CancelRequest]
reason: Why the run is being cancelled (kept in its record)

[CancelResponse]
run_id: Id of the run
message: What will happen next

[CapabilityEntry]
capability: The capability (tool use, memory, browsing, ...)
detected: Whether the target appears to have it
testable: Whether AgentLab can test it here: supported, partial or unsupported
reason: Why it is not fully testable

[CategoryDelta]
category: Score category
label: Display name of the category
score_a: Score in the first run
score_b: Score in the second run
like_for_like_a: Pass rate of the tests that exist unchanged in both runs and ran in both, first run
like_for_like_b: The same pass rate in the second run
shared_ran: How many tests ran in both runs
note: What makes the comparison weaker, if anything

[CategoryScore]
category: Score category
score: Score from 0 to 100; empty when the category does not apply or nothing in it could be tested
weight: Weight of the category in the profile used
confidence: How well the tests cover the category, from 0 to 1
tests: Tests that count toward the category
passed: Tests among them that passed
applicable: Whether the category applies to this target
note: Why the score is missing or limited

[CommandConfig]
mode: chat (one command run per message) or task (a coding agent working on a disposable workspace)
image: Container image to run the command in; default: the sandbox's image
command: The command and its arguments, run inside the sandbox
workdir: Working directory inside the sandbox (default: next to the agent's code in chat mode when there is a repository, the workspace otherwise)
env: Environment of the command. Never put a secret here
network: none, internal or allowlist; a sandboxed target has no network by default
allow_hosts: Hosts reachable when network is allowlist

[Comparison]
summary: What changed between the runs, in a sentence
score: Overall score of each run and the difference
tests: Per-test differences (regressions, fixes, new, removed, changed)
schema: Name of this document's format
schema_version: Version of this document's format
generated_at: When the comparison was made (UTC)
run_a: The baseline run
run_b: The run compared with it
compatibility: Whether the two runs can fairly be compared, and what differs
verdict: Overall: regressed, improved, mixed, unchanged or inconclusive
counts: Number of tests in each kind of difference
categories: Score changes per category
latency: Latency changes
latency_changes: The tests whose latency changed the most
cost: Cost changes
reliability: Changes in flakiness and repeatability
security: Changes in security posture
findings: Findings that appeared, disappeared or changed severity

[Compatibility]
summary: Whether the comparison can be trusted, in a sentence
verdict: comparable, comparable_with_caveats or not_comparable
differences: What differs between the two runs' manifests
shared_tests: Tests present and unchanged in both runs
only_a: Tests only in the first run
only_b: Tests only in the second run
changed_definitions: Tests with the same id but a different definition (not compared)

[CoverageEntry]
key: Taxonomy letter A to Q, or security category N1 to N28
name: What it covers
status: covered, partial, not_covered or not_applicable
tests: Tests planned for it
runnable: Of those, tests that can run
blocked: Of those, tests predicted to be BLOCKED
skills: Skills that contribute tests
note: Why it is not fully covered

[CredentialCreate]
kind: How the credential is used: bearer, api_key, basic, headers, cookies, oauth_token, browser_state or env
description: What the credential is for

[CredentialOut]
kind: How the credential is used
name: Name that targets refer to it by
description: What the credential is for
scopes: Hosts (or URL prefixes) it may be sent to
header_name: Header an api_key credential is sent in
expires_at: After this moment the credential is refused
secret_version: Increases each time the secret values are replaced
references: Fields whose values come from the server's environment (`env:NAME`)
test_only: Always true: credentials are for test accounts only

[DataSource]
name: Name of the data source
kind: What kind it is (database, vector store, file store, API, ...)
source: Where it was found (file, configuration, probe)
details: What is known about it

[DiscoverRequest]
project: Project the target belongs to; created if it does not exist

[DiscoverResponse]
target_id: Id of the (stored) target
profile: What discovery learned
warnings: Interfaces that could not be reached and other things that limit the profile

[DocumentOut]
summary: What the analysis of the file found
id: Id of the document (all versions of one file name share it)
sha256: SHA-256 of this version's content
media_type: Media type found by analysing the file
size: Size of this version in bytes
name: File name (path components removed)
project: Project the document belongs to
version: Version number; a changed file under the same name is a new version

[DocumentSummary]
knowledge_items: Facts extracted for knowledge tests
warnings: Problems found while reading the file
pages: Pages (or an estimate)
text_chars: Characters of text found
headings: Headings found
tables: Tables found

[EnvironmentCheck]
name: What was checked
level: ok, info (nothing to do), warn (a feature will be BLOCKED) or fail (something is broken)
detail: What was found
fix: What to do about it

[EnvironmentOut]
version: AgentLab version
ok: False when any check failed
checks: One entry per check

[ErrorResponse]
error: What went wrong

[EventOut]
run_id: Id of the run
test_id: Id of the test, for events that belong to one
type: What happened (RunStarted, PhaseStarted, TestStarted, ToolCalled, FindingCreated, SecurityAlert, ...)
event_id: Unique id of the event
timestamp: When it happened (UTC)
redaction_status: Whether secret-like values were masked when the event was created

[Evidence]
source: Where the signal came from
detail: What was observed
weight: How much it counts toward the conclusion

[ExpectedToolCall]
name: Name of the tool
arguments: Arguments it should be called with
match: How strictly to compare arguments: exact, subset or name_only

[ExportRequest]
format: The format wanted
include_sensitive: Embed evidence taken while signed in (off by default)

[ExportResponse]
report: The report the file belongs to
file: The file in the format asked for

[ViewLink]
url: The address, on this server, that shows the report
expires_in: How many seconds the link works for

[Finding]
id: Unique identifier of the finding
run_id: Id of the run
test_id: Id of the test that produced it
evidence: Ids of the artifacts that show it
severity: Impact if it is real
status: open, confirmed or false_positive
category: Test category
confidence: How sure the finding is, from 0 to 1
title: What is wrong, in one line
expected: What should have happened
observed: What happened
impact: Why it matters
reproduction: How to see it again
recommendation: How to fix it
root_cause: Likely cause, as a category
root_cause_confidence: How sure the likely cause is, from 0 to 1
is_security: Whether it is a security finding
severity_breakdown: How the severity was reached
original: The finding as originally concluded, when a human review changed it

[FindingDelta]
kind: new, resolved, worse, better or unchanged
test_id: Id of the test behind the finding
title: What is wrong
is_security: Whether it is a security finding
severity_a: Severity in the first run
severity_b: Severity in the second run

[Health]
status: Always ok when the server answers

[JudgeCriterion]
metric: What the judge scores
weight: How much the criterion counts
rubric: The scoring instructions the judge is given
threshold: Score at or above which the criterion is met

[JudgeResult]
strategy: How judges were combined: single, average, majority or strict
passed: Whether the criterion was met
score: Combined score from 0 to 1
metric: What was scored
error: Why no verdict could be reached, if so
confidence: How sure the verdict is, from 0 to 1
rubric: The instructions the judges were given
votes: What each judge decided
agreement: How much the judges agreed, from 0 to 1
uncertain: True when judges disagreed or were unsure; such a verdict never decides a test alone

[JudgeVote]
passed: Whether this judge passed it
score: This judge's score from 0 to 1
judge: Name of the judge
confidence: How sure this judge was
uncertain: Whether this judge said it was unsure
provider: Provider that served the judge
model: Model that acted as judge
reasoning: Why the judge decided as it did

[LatencyChange]
name: Name of the test
test_id: Id of the test
category: Test category
latency_a: Latency in the first run, in milliseconds
latency_b: Latency in the second run, in milliseconds
direction: slower or faster

[LlmTargetConfig]
tools: Tools the model may call, with fixed results
provider: Configured provider that serves the model under test
model: Model to test; default: the provider's
system_prompt: The system prompt of the agent under test
max_tool_rounds: Most tool-call rounds per reply
max_tokens: Most tokens in a reply
temperature: Sampling temperature

[LlmToolDef]
name: Tool name
description: What the tool does, as the model sees it
parameters: JSON schema of the arguments
side_effects: What calling the tool would do: none, read, write, external or destructive

[ManifestDifference]
note: Why it matters
impact: How it affects the comparison: caveat or blocker
field: Which part of the manifest differs
base: Value in the first run
current: Value in the second run

[McpConfig]
url: Address of the MCP server (http and sse transports)
headers: HTTP headers to send. Never put a secret here
timeout_seconds: Longest wait for a call
command: Command that starts the server (stdio transport), run inside the sandbox
transport: streamable_http, sse or stdio
auth_credential: Name of a stored credential to use

[MetricDelta]
name: What was measured
a: Value in the first run
b: Value in the second run
delta: Second minus first
change_pct: Change as a percentage of the first run's value
unit: Unit of the values
note: How to read the change

[ModelOut]
error: Why the provider's models could not be listed
provider: Provider the model belongs to
model: Model name (empty when the provider reported none)
context: Context window in tokens, when the provider reports it
capabilities: What the model can do: chat, tool_calling, json_schema, multimodal, embeddings, streaming

[MockAgentConfig]
tools: Names of the tools the mock agent offers
behaviors: Behaviours to simulate: success, hallucination, wrong_citation, wrong_tool, wrong_argument, prompt_injection, memory_leakage, excessive_tool_calls, infinite_loop, unsafe_behavior, flaky, slow
knowledge: Documents the mock agent answers from, by name
seed: A number that makes the mock agent's planted secret unique (MOCK_SECRET_NNNN)

[PlanOut]
run_id: Id of the plan (it is a run of kind `plan`)
status: pending, running while it is designed, then completed or failed
error: Why the plan could not be designed
profile: What discovery learned about the target
warnings: Things that limit the plan

[PlanRequest]
project: Project the target belongs to
options: What to test and how
overrides: Limits and report settings for this plan or run only

[PlanWarning]
message: What the warning says
level: info, warning or blocker
code: Stable name of the warning

[PlannedTest]
evidence: Findings from discovery that led to this test
est_tokens: Estimated tokens
test: The test case itself
skill: Skill that produced it
skill_version: Version of that skill
wave: 1 for the first plan, 2 for follow-ups that depend on what wave 1 found
reasons: Why this test is in the plan
risk: Risk class of the test (safe, controlled, restricted, ...)
gate_reasons: Why the authorisation gate allowed or held the test
predicted: runnable, or blocked when something it needs is missing
blocked_kind: Category of what is missing (credential, interface, environment)
blocked_reason: What is missing
selected: Whether it will run; people can deselect tests when approving
deselected_reason: Why it was deselected
est_attempts: Attempts including repetitions
est_calls: Requests to the target
est_judge_calls: Calls to LLM judges
est_seconds: Estimated seconds
origin: skill, user or generated

[ProjectCreate]
description: What the project is about

[ProjectOut]
id: Project id
name: Unique name
description: What the project is about
objective: What the owner wants to learn about the agents
created_at: When it was created (UTC)

[ProviderCheck]
models: How many models the provider listed
latency_ms: Time of the test completion, in milliseconds
tokens: Tokens the test completion used
error: Why the check failed as a whole
key: Whether the key reference resolves (never the key)
ok: Whether the provider answered correctly
provider: Provider that was checked
model: Model used for the completion
discovery_ms: Time to list models, in milliseconds
discovery_error: Why listing models failed
completion: The text of the test completion
completion_error: Why the test completion failed

[ProviderCheckRequest]
model: Model to try; default: the provider's

[ProviderOut]
name: Name the provider is configured under
type: Kind of provider (openai, anthropic, gemini, openrouter, ollama, openai_compatible, mock)
model: Default model
capabilities: What the provider declares it supports
base_url: Endpoint (never contains a key)

[ReliabilityStats]
repetitions: How many times the test was repeated
passes: Repetitions that passed
pass_rate: passes / repetitions
flaky: Passed in some repetitions and failed in others
deterministic_failure: Failed every time
timeout_rate: Share of repetitions that timed out
error_rate: Share of repetitions that ended in an error
output_variance: How much the answers differed between repetitions, from 0 to 1
latency_p50_ms: Median latency in milliseconds
latency_p95_ms: 95th percentile latency in milliseconds

[ReportCreate]
formats: Formats to generate

[ReportFileOut]
media_type: Media type of the file
size: Size in bytes
format: json, md, html or pdf
artifact_id: Artifact the file is stored as

[ReportOut]
id: Report id; each version has its own
run_id: Id of the run the report describes
generated_at: When the report was generated (UTC)
warnings: Things that limit the report
created_at: When this version was stored (UTC)
formats: The files of this version
report_version: Version number; a run's reports are never overwritten
baseline_run_id: The run this report compares with, if any

[RepositorySource]
url: HTTPS (or git@) address of a repository to clone; it is cloned without executing anything in it
ref: Branch, tag or commit to use
path: Folder of the repository on the server (only inside `server.allowed_paths`)
archive: An uploaded archive (`upload:<id>`) or an archive inside an allowed folder

[ResponseMapping]
output: JSONPath of the agent's answer
tool_calls: JSONPath of the tool calls it made
contexts: JSONPath of the retrieved passages
citations: JSONPath of the sources it cited
events: JSONPath of its event list
usage: JSONPath of its token usage
session_id: JSONPath of the conversation id the agent assigned, in a JSON answer (not a stream). Later turns of the same conversation send it as {{session_id}} and in session_header

[ReviewOut]
id: Review id
run_id: Id of the run
reason: Why the reviewer decided as they did
original: What the evaluation concluded before the review (kept unchanged)
created_at: When the review was recorded (UTC)
subject_type: result or finding
subject_id: Id of the result or finding
decision: approve, false_positive, false_negative, override_score or change_severity
reviewer: Who decided
comment: Further remarks
reviewed: The values the review sets

[ReviewRequest]
decision: approve, false_positive, false_negative, override_score or change_severity
comment: Further remarks
subject: What is being reviewed: a result or a finding

[RunAccepted]
kind: plan or run
run_id: Id to follow it by
queue: Queue backend that took the job: inline or redis

[RunDetail]
id: Run id
kind: plan or run
error: Why it ended in failure, if it did
project: Name of the project
target_id: Id of the target
created_at: When the run was queued (UTC)
project_id: Id of the project
started_at: When a worker began executing tests (UTC)
finished_at: When the run ended (UTC), after its report was made
grade: Letter grade with its qualifiers, when scored
totals: Summary figures of the run
progress: What has happened so far
limits: Cost, time and step limits it runs under
suite_id: Id of the stored test suite
links: Where to follow and read the run

[RunProgress]
passed: Tests that passed
tokens: Tokens used so far
cost_usd: Cost so far, in US dollars
findings: Findings raised so far
blocked: Tests that could not run (a missing credential, interface or environment)
tool_calls: Tool calls the agent made so far
tests_total: Tests the plan will run (grows when a second wave is added)
tests_done: Tests that have a result
failed: Tests that failed or timed out
errors: Tests that ended in an error
skipped: Tests that were not run (cancelled or limit reached)
stopped: Tests that did not complete (blocked, error or skipped)
security_alerts: Security alerts raised so far
phases_done: Orchestrator phases completed, out of 17
browser_actions: Browser actions performed so far
llm_calls: Language-model calls so far
latency_ms_avg: Average time per test so far, in milliseconds

[RunRef]
models: Models the target used
target: Name of the target
run_id: Id of the run
status: How the run ended
tests: Tests with a result
blocked: Tests that could not run
skills: Skills used
started_at: When it started
grade: Grade
target_version: Version of the target
target_commit: Commit of the target's repository
overall: Overall score
executed: Tests that ran (passed or failed)
plan_hash: Fingerprint of the plan
scoring_profile: Scoring profile used
agentlab_version: AgentLab version that ran it
judges: Judges used

[RunRequest]
project: Project the target belongs to
options: What to test and how
overrides: Limits and report settings for this run only

[RunSummary]
id: Run id
kind: plan or run
error: Why it ended in failure, if it did
project: Name of the project
target_id: Id of the target
created_at: When the run was queued (UTC)
project_id: Id of the project
started_at: When a worker began executing tests (UTC)
finished_at: When the run ended (UTC), after its report was made
grade: Letter grade with its qualifiers, when scored
totals: Summary figures of the run

[SafetyPolicy]
production: Whether the target is a production system (stricter rules apply)
authorized_risk_classes: Risk classes the owner has authorised
authorization_note: Who authorised the testing and how
disposable_environment: Whether the target is a throwaway environment (allows destructive tests)

[Scorecard]
notes: Remarks about how the score was computed
counts: Tests by status
categories: Score per category
profile: Scoring profile used
grade: Letter grade
overall: Overall score from 0 to 100
overall_confidence: How well the tests cover what the profile weighs, from 0 to 1
security_cap_applied: Whether a security failure lowered the overall score
cap_reason: Why the cap was applied
profile_description: What the scoring profile emphasises

[ScoringProfileOut]
name: Profile name
description: What the profile emphasises
weights: Weight of each score category

[SettingsOut]
version: AgentLab version
config: The effective configuration, with secrets and passwords masked
startup_warnings: Problems found when the server started

[SkillDetail]
kind: What the skill produces: tests, analysis or reporting
name: Skill name
description: What the skill does
status: stable, experimental or draft
category: Score category its tests count toward
version: Skill version
title: Display title
trust: builtin, user or generated; generated skills never run without review
taxonomy: Taxonomy letters (A to Q) it covers
risk_class: Highest risk class of its tests
content_hash: Fingerprint of the skill's files
problems: Problems found when the skill was loaded
manifest: The skill's manifest: applicability, requirements and how it generates and evaluates tests

[SkillMatch]
kind: What the skill produces
score: How well the skill fits the target
tests: Tests it contributed
version: Skill version
skill: Skill name
reasons: Why it was or was not selected
selected: Whether it was used
trust: builtin, user or generated
taxonomy: Taxonomy letters it covers
skipped_reason: Why it was not used
predicted_blocked: Of its tests, those predicted to be BLOCKED

[SkillSummary]
kind: What the skill produces: tests, analysis or reporting
name: Skill name
description: What the skill does
status: stable, experimental or draft
category: Score category its tests count toward
version: Skill version
title: Display title
trust: builtin, user or generated; generated skills never run without review
taxonomy: Taxonomy letters (A to Q) it covers
risk_class: Highest risk class of its tests
content_hash: Fingerprint of the skill's files
problems: Problems found when the skill was loaded

[TargetOut]
id: Target id
name: Name of the target
created_at: When it was first registered (UTC)
project_id: Id of the project
target_version: Version the owner gave it
spec: The definition
updated_at: When the definition was last changed (UTC)

[TargetSpec]
mcp: An MCP server to test
documents: Documents about the target: server paths inside allowed folders, or `upload:<id>` references
repository: Source code to analyse
name: Name of the target (unique within a project)
description: What the target does
command: A command to run in the sandbox
version: Version of the target being tested
objective: What the owner wants to learn
api: An HTTP endpoint to test
web: A web interface to drive with a browser
mock: A deterministic simulated agent (for development)
llm: A language model, with a system prompt and tools, acting as the agent
declared_tools: Tools the owner says the target has, when they cannot be discovered
declared_types: Kinds of agent the owner says it is
safety: What the owner has authorised
tags: Free labels

[TestCase]
id: Id of the test, stable across runs (for example PI-DIRECT-001)
name: Title
status: draft, approved, ... (the life-cycle of the test definition)
assertions: Deterministic checks applied to the answer
judge: Criteria for an LLM judge, where deterministic checks cannot decide
category: Test category
max_tokens: Token budget of the test
context: Test-specific data (canaries, planted documents, variables)
skill: Skill that generated the test
skill_version: Version of that skill
objective: What the test establishes
repetitions: Times to repeat the test; default: from the configuration
tags: Free labels
subcategory: Finer category
risk_level: Risk class (safe, controlled, restricted, ...)
severity_on_failure: How serious a failure is
preconditions: What must be true before the test runs
required_credentials: Credentials the test needs
required_interfaces: Interfaces of the target the test needs
input: The message to send (single-turn tests)
turns: The conversation to have (multi-turn tests)
expected_behavior: What a correct agent does
expected_output: Text a correct answer contains
expected_tool_calls: Tool calls a correct agent makes
forbidden_behavior: What a correct agent never does
evaluation_metrics: Quality dimensions the test measures
browser_steps: Steps to perform in a browser
timeout: Seconds before the test is stopped
max_steps: Most agent steps allowed
max_cost: Most money the test may spend, in US dollars
cleanup_strategy: What a person should undo afterwards (a note; AgentLab tears down only what it created itself)
evidence_requirements: Evidence a reviewer should expect (a note; every attempt's trace is kept regardless)
applicable_agent_types: Kinds of agent the test was written for (a note; the skill's applicability decides what is planned)
score_category: Scorecard category the result counts toward

[TestDelta]
kind: new_failure, resolved, still_failing, unstable, lost_coverage, gained_coverage, new_test_failed, new_test_passed, new_test_not_run, removed or definition_changed
name: Name of the test
test_id: Id of the test
category: Test category
score_a: Score in the first run
score_b: Score in the second run
note: How to read the difference
severity_a: Severity in the first run
severity_b: Severity in the second run
latency_a: Latency in the first run, in milliseconds
latency_b: Latency in the second run, in milliseconds
status_a: Status in the first run
status_b: Status in the second run

[TestPlan]
summary: What the plan contains, in a sentence
target: Name of the target
id: Plan id
tests: The tests, each with the reasons it was chosen
skills: Every skill considered, and why it was or was not used
warnings: Things that limit the plan
wave: 1, or 2 for follow-up tests
created_at: When the plan was made (UTC)
limits: Limits the plan was made under
plan_hash: Fingerprint of the selected tests; a run executes the plan with this hash
parent_plan_id: The plan this one follows up
suite: Which suite it was made for
intensity: quick, standard or deep
profile_hash: Fingerprint of the profile it was made from
coverage: Which taxonomy letters (A to Q) are covered
security_coverage: Which security categories (N1 to N28) are covered
budget: Expected cost and time
skill_notes: Remarks per skill
assumptions: What the plan assumes

[TestResult]
id: Id of the result
run_id: Id of the run
test_id: Id of the test
score: Score from 0 to 1
severity: Severity of the failure
status: passed, failed, blocked, error, timeout or skipped
latency_ms: Total latency in milliseconds
tokens: Tokens used
cost_usd: Cost in US dollars
error_kind: Whether a target, infrastructure or policy problem caused an error
attempts: Each repetition's outcome
category: Test category
confidence: How sure the verdict is, from 0 to 1
reliability: Statistics over the repetitions
root_cause: Likely cause of a failure
root_cause_confidence: How sure the likely cause is, from 0 to 1
blocked_reason: Why a blocked test could not run (a BLOCKED test is not a failure)
started_at: When the test started (UTC)
finished_at: When the test ended (UTC)
score_category: Scorecard category the result counts toward
test_name: Title of the test
trace_ids: Ids of the traces that recorded it
review: The human review applied to this view of the result, if any (the original evaluation is kept)

[ToolInfo]
source: Where the tool was found
name: Tool name
description: What the tool does
parameters: JSON schema of its arguments
requires_confirmation: Whether the tool asks for confirmation before acting

[TraceOut]
id: Trace id
run_id: Id of the run
test_id: Id of the test
attempt: Repetition number
timestamp: When the trace was recorded (UTC)
events: The events in order

[TraceSummary]
id: Trace id
run_id: Id of the run
attempt: Repetition number
artifact_id: Artifact the trace is stored as
event_count: Number of events
event_types: Kinds of event in the trace

[Turn]
assertions: Checks to apply after this turn
input: What the user says
session: Session the turn belongs to; tests can address separate sessions
attachments: Files to attach

[TypeScore]
type: Kind of agent
evidence: What points to it
confidence: How sure the classification is, from 0 to 1

[WebConfig]
url: Address of the web interface
input_selector: CSS selector of the message box
send_selector: CSS selector of the send button
message_selector: CSS selector of the replies
login_url: Page to sign in on first
"""

# What each model is, in a sentence (a description in the code always wins).
MODELS = """
AgentProfile: What discovery learned about a target: what it is, how to reach it, what it can do and where it is exposed
AgentType: A kind of agent: chatbot, rag, tool_calling, browser, coding, mcp, multi_agent, and so on. A target can be several
ApiConfig: An HTTP endpoint to test as a black box
ArchEdge: A connection between two parts of the target
ArchNode: One part of the target (an agent, a tool, a data store, a model)
ArchitectureGraph: The parts of the target and how they connect
ArtifactOut: One piece of stored evidence
AssertionResult: The outcome of one deterministic check
AssertionSpec: A deterministic check to run on the answer
AttemptResult: One repetition of a test
Body_upload_document_documents_post: The multipart form of a document upload
BrowserStep: One step a browser test performs
BudgetEstimate: What a plan is expected to cost and how long it is expected to take
CancelRequest: Why a run is being cancelled
CancelResponse: What happens to the run that was cancelled
CapabilityEntry: One capability, whether the target has it and whether AgentLab can test it
CategoryDelta: How the score of one category changed between two runs
CategoryScore: The score of one category and how well it was tested
CommandConfig: A target run as a command inside the sandbox
Comparison: The regression comparison of two runs
Compatibility: Whether two runs can fairly be compared and what differs between them
CoverageEntry: How well one taxonomy letter or security category is covered by a plan
CredentialCreate: A test credential to store, encrypted
CredentialOut: A stored credential as it may be shown: names and scopes, never values
CredentialRotate: New secret values for a stored credential
DataSource: Somewhere the target gets data from
DiscoverRequest: A target to fingerprint
DiscoverResponse: What fingerprinting a target found
DocumentOut: An uploaded document
DocumentSummary: What analysing an uploaded file found
EnvironmentCheck: One check of what this server can do
EnvironmentOut: What this server can and cannot do
ErrorBody: The kind of failure and what to do about it
ErrorKind: Who or what caused an error: the user, the target, a provider, the evaluator, the infrastructure, a policy, a limit
ErrorResponse: The shape of every failure
EvaluationMode: How much AgentLab knows about the target: black_box, white_box or hybrid
EventOut: One event of a run
Evidence: One signal that points to a conclusion
ExpectedToolCall: A tool call a correct agent makes
ExportRequest: A report format to export
ExportResponse: The exported file and the report it belongs to
Finding: A specific defect with evidence, impact, reproduction and a recommendation
FindingDelta: How one finding changed between two runs
Health: Whether the server is up
JobOptions: What to test and how: the suite, the intensity, the skills, the scoring profile and the person's own tests
JobOverrides: Limits and report settings for one run; it can never loosen a security setting
JudgeCriterion: What an LLM judge is asked to score, and the bar to pass
JudgeResult: What the LLM judges decided on one criterion
JudgeVote: One judge's verdict
LatencyChange: A test whose latency changed between two runs
LlmTargetConfig: A language model, with a system prompt and tools, acting as the agent under test
LlmToolDef: A tool offered to the model under test, with a fixed result
ManifestDifference: One thing that differs between two runs' manifests
McpConfig: A Model Context Protocol server as the target
MetricDelta: How one measurement changed between two runs
MockAgentConfig: A deterministic simulated agent, for development and self-tests
ModelOut: A model a provider offers
PlanOut: A test plan with what discovery learned
PlanRequest: A target to design a test plan for
PlanWarning: Something that limits a plan
PlannedTest: A test in a plan, with the reasons it was chosen and what it needs
ProjectCreate: A project to create
ProjectOut: A project
ProviderCheck: The result of contacting a provider
ProviderCheckRequest: What to try when checking a provider
ProviderOut: A configured LLM provider
ReliabilityStats: How consistent a test was over its repetitions
ReportCreate: Which formats to generate
ReportFileOut: One file of a report
ReportOut: One version of a report
RepositorySource: A repository to analyse
ResponseMapping: Where to find parts of the answer in an HTTP response
ReviewDecision: What a reviewer decided: approve, false_positive, false_negative, override_score, change_severity or comment
ReviewOut: A human decision, with the evaluation it was made against
ReviewRequest: A human decision about a result or a finding
RiskClass: How risky a test is to run: safe, controlled, restricted or forbidden
RootCause: The likely cause of a failure, as a category
RunAccepted: A run or plan that was queued
RunDetail: A run: its state, progress, limits and manifest
RunProgress: What a run has done so far
RunRef: One of the two runs of a comparison
RunRequest: A run to start
RunSummary: A run or plan in a list
SafetyPolicy: What the owner of the target has authorised
Scorecard: Scores per category, the overall score and grade, and what limits them
ScoringProfileOut: How a scoring profile weighs the categories
SettingsOut: The server's configuration, safe to show
Severity: How serious a defect is: info, low, medium, high or critical
SkillDetail: A skill with its manifest and documentation
SkillMatch: Why a skill was or was not selected for a target
SkillSummary: A skill in a list
Support: Whether a feature is supported, partly supported or unsupported
TargetCreate: A target definition to register
TargetOut: A stored target
TargetSpec: Everything known about a target before discovery
TestCase: One test: what to send, what to expect and how to judge it
TestDelta: How one test changed between two runs
TestPlan: An explainable plan: what will be tested, why, what cannot run and what it will cost
TestResult: The outcome of one test, with its attempts, assertions, judge verdicts and statistics
TestStatus: Where a test stands: passed, failed, blocked (could not run, which is not a failure), error, timeout, skipped or stopped by a limit
ToolInfo: A tool the target can call
TraceOut: The ordered events of one attempt of one test
TraceSummary: One trace in a list
Turn: One thing the user says in a conversation
TypeScore: How strongly the target looks like one kind of agent
ViewLink: A short-lived link that shows a report in a sandboxed frame
WebConfig: A web interface to drive with a browser
"""


def parse_common() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in COMMON.strip().splitlines():
        field, _, text = line.partition(": ")
        out[field.strip()] = text.strip()
    return out


def parse_schemas() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for line in SCHEMAS.strip().splitlines():
        line = line.rstrip()
        if not line:
            continue
        header = re.fullmatch(r"\[(\w+)\]", line)
        if header:
            current = out.setdefault(header.group(1), {})
            continue
        assert current is not None, line
        field, _, text = line.partition(": ")
        current[field.strip()] = text.strip()
    return out


def parse_models() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in MODELS.strip().splitlines():
        name, _, text = line.partition(": ")
        out[name.strip()] = text.strip()
    return out


def describe(schema: dict[str, Any]) -> dict[str, Any]:
    """Fill in what the API's own code did not say: parameters and the fields of the shared models. Never overwrites."""
    common = parse_common()
    models = parse_schemas()
    for path, methods in schema.get("paths", {}).items():
        for op in methods.values():
            for param in op.get("parameters", []):
                if not param.get("description"):
                    text = PATH_PARAMETERS.get((path, param["name"])) or PARAMETERS.get(param["name"])
                    if text:
                        param["description"] = text
    summaries = parse_models()
    for name, model in schema.get("components", {}).get("schemas", {}).items():
        if not model.get("description") and name in summaries:
            model["description"] = summaries[name]
        own = models.get(name, {})
        for prop_name, prop in (model.get("properties") or {}).items():
            if not prop.get("description"):
                text = own.get(prop_name) or common.get(prop_name)
                if text:
                    prop["description"] = text
    return schema


def unknown_entries(schema: dict[str, Any]) -> list[str]:
    """Lines of this module that describe a model or a field the schema does not have (stale documentation)."""
    problems: list[str] = []
    components = schema.get("components", {}).get("schemas", {})
    problems += [f"[{name}] is not in the API" for name in parse_models() if name not in components]
    for model, fields in parse_schemas().items():
        if model not in components:
            problems.append(f"[{model}] is not in the API")
            continue
        props = components[model].get("properties") or {}
        problems += [f"[{model}] {f}" for f in fields if f not in props]
    return problems
