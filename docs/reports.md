# Reports, comparison and human review

What a finished run leaves behind for a person to read, check and share: the report bundle and its four formats, how to read
a finding, how to compare two runs, and how a reviewer's opinion is kept next to the measurement without replacing it.

* [Write a report](#write-a-report)
* [The report bundle](#the-report-bundle): layout, versions and checksums
* [The four formats](#the-four-formats)
* [The 27 sections](#the-27-sections)
* [Reading a result](#reading-a-result) and [a finding](#reading-a-finding)
* [Evidence](#evidence)
* [Comparing runs](#comparing-runs)
* [Human review](#human-review)
* [Through the API](#through-the-api) and [in the web interface](#in-the-web-interface)
* [Configuration](#configuration) and [what reports do not do](#what-reports-do-not-do)

Every command and output below was produced by AgentLab 0.1.0 against the bundled example agent *Acme Assistant*
(`agentlab fixtures serve chatbot`), once with all its defects (**before**) and once with none (**after**). Ids, dates and
timings differ on your machine.

## Write a report

`agentlab test` writes the formats named in `reporting.formats` when the run ends (by default all four; `--report none`
writes none). `agentlab report` writes them for any finished run later, from what is stored:

```
$ agentlab report --run 9c266797
Report run 9c266797 · version 1 · bundle 8a71076b8b30
  json  .agentlab/reports/9c266797-670c-47fd-b240-9d82ff111a89/v1/report.json
  md    .agentlab/reports/9c266797-670c-47fd-b240-9d82ff111a89/v1/report.md
  html  .agentlab/reports/9c266797-670c-47fd-b240-9d82ff111a89/v1/report.html
  pdf   .agentlab/reports/9c266797-670c-47fd-b240-9d82ff111a89/v1/report.pdf
  checksums: .agentlab/reports/9c266797-670c-47fd-b240-9d82ff111a89/v1/checksums.json
  verify:    agentlab report --verify .agentlab/reports/9c266797-670c-47fd-b240-9d82ff111a89/v1
```

| Option | Meaning |
|---|---|
| `--run`, `-r` | The run (an id prefix is enough). Default: the latest run. |
| `--format`, `-f` | `json`, `md`, `html`, `pdf` or `all`; repeat or separate with commas (`md,json`). Aliases `markdown` and `htm` work. Anything else is refused: *unknown report format 'docx' (use json, md, html, pdf or all)*. |
| `--output`, `-o` | A folder for the bundle instead of the configured reports folder. |
| `--baseline` | A run to compare with; adds the [comparison](#comparing-runs) to the report. Default: the baseline the run was started with (`agentlab test --baseline`). |
| `--include-sensitive` | Embed [restricted evidence](#evidence). Off by default. |
| `--verify PATH` | Check a bundle against its `checksums.json` and exit. |
| `--json` | Print what was written as JSON (run, report id and version, folder, files, redaction counts and warnings). |

A report can be written for a finished, stopped or failed run, and it says how the run ended. A run that has produced no
result yet is refused (*run 'X' has not produced any result yet*), and so is `agentlab report` with no run at all (*there is
no finished run yet: run `agentlab test` first, or give --run*). A run in which nothing could be tested still gets an
honest report: it says that nothing was tested and gives no grade.

## The report bundle

One report is a folder; every report of a run is a new folder.

```
.agentlab/reports/<run id>/
  v1/  report.json  report.md  report.html  report.pdf  run-manifest.json  checksums.json
  v2/  report.json  report.md  checksums.json  run-manifest.json
```

* **Versions are never overwritten.** Writing a report of the same run again creates `v2`, then `v3`. This is also how a
  report that includes a [review](#human-review) differs from the one written before it: both stay on disk.
* `run-manifest.json` records what produced the numbers: the AgentLab version and commit, the target (name, version, a hash
  of its definition), the plan hash and the skills used with their content hashes, the scoring profile, the judges, the
  evaluation settings and limits, and whether Docker and a browser were available.
* `checksums.json` lists every file with its size and SHA-256:

  ```json
  {
    "agentlab_version": "0.1.0",
    "files": {
      "report.html": {"bytes": 543827, "sha256": "abc70fdf38d1…"},
      "report.json": {"bytes": 328776, "sha256": "43b1819c19b0…"}
    },
    "report_version": 1,
    "run_id": "9c266797-670c-47fd-b240-9d82ff111a89",
    "schema": "agentlab.report-bundle",
    "schema_version": 1
  }
  ```

`agentlab report --verify` recomputes them. A bundle that is as written says so; one that was changed, trimmed or added to
does not, and the command exits with 1:

```
$ agentlab report --verify .agentlab/reports/9c266797-…/v1
ok .agentlab/reports/9c266797-…/v1 matches its checksums

$ agentlab report --verify tamper/v1
x report.html: checksum does not match (the file was changed after it was written)
x extra.txt: not in checksums.json (added after the bundle was written)
```

The checksums show that a file changed since it was written. They are not a signature: anyone who can edit the folder can
rewrite `checksums.json` as well, so keep a bundle you must prove somewhere you trust.

## The four formats

All four are rendered from the same data. `report.json` is that data; the others never contain a number the JSON does not.

| Format | What it is for | Notes |
|---|---|---|
| **JSON** `report.json` | Machines, dashboards, CI, your own tooling. | Schema `agentlab.report`, version 1 (`schema`, `schema_version`, `report_version`, `generated_at`). The data of each section under a named key (`executive`, `scorecard`, `risk`, `security`, `inventory`, `results`, `findings`, `regression`, `evidence`, …), plus `reviews`, `versioning` (what produced the run) and `reviewed_scorecard` when reviews exist. |
| **Markdown** `report.md` | Reading in a terminal, a pull request or a wiki. | Everything that came from the target is escaped or fenced, so a target's output cannot become markup. |
| **HTML** `report.html` | The report people open. | **One self-contained file**: styles, the data and inline SVG charts are in it, and it makes no request to anywhere (`default-src 'none'` in its Content-Security-Policy; the only script is pinned by its hash). It is complete without JavaScript; the script adds search, filtering and sorting of tests, a test matrix that filters on click, a light/dark switch, print and a "download JSON" button. |
| **PDF** `report.pdf` | Printing and attaching. | Drawn with reportlab: no browser, no network. The same 27 sections, a table of contents with page numbers and a PDF outline, charts as vector graphics. A Unicode TrueType font is used when one is found (DejaVu, Liberation or Arial; set `AGENTLAB_PDF_FONT_DIR` to say where); without one the built-in Latin-1 fonts are used and characters they cannot show are replaced by close ASCII forms, so a missing font changes how it looks and never what it says. The same report data gives the same bytes (no clock, no random ids); a report written later carries its own generation time. |

## The 27 sections

Every report has the same 27 sections in the same order, in every format, so a section is never "missing": one that does not
apply says why.

| # | Section | What it holds |
|---|---|---|
| 1 | Executive summary | Grade and score, how many tests passed, strengths, concerns and gaps, **what limits the result**, and what to do first. |
| 2 | Overall score | The score by category with weights, tests, confidence and notes; the qualifiers that must be read with it; the reviewed score when there are reviews. |
| 3 | Risk summary | Open findings by severity and the security posture. |
| 4 | Target overview | Name, version, interfaces, evaluation mode (black box or white box), models and frameworks found. |
| 5 | Target architecture | A diagram of what was seen. Components that were not seen are not drawn. |
| 6 | Agent type classification | Each type with its confidence and the evidence for it ([testing-agents.md](testing-agents.md#discovery)). |
| 7 | Capability matrix | Each capability: detected or not, testable or not, and why. |
| 8 | Environment | Sandbox, browser, judge, interfaces tested, parallelism, versions. |
| 9 | Test methodology | How the run was done and the three evaluation layers. |
| 10 | Test suite inventory | Tests planned, selected and executed; by skill, category and wave. |
| 11 | Test results | Every test: status, score, severity, latency, note. A `BLOCKED` test shows its reason. |
| 12 | Failed tests | Each failed test: input, why it failed, the observed output and how to reproduce it. |
| 13 | Security findings | The 28 categories with their verdicts, the findings in the [format below](#reading-a-finding), and what the security tests could not show. |
| 14–18 | RAG, tool, memory, browser and multi-agent evaluation (with planning, coding and MCP) | Results for what the target does; *"Not applicable: the target shows no sign of …"* for what it does not. |
| 19 | Reliability | Verdict, which tests were repeated, which failed every time, which were flaky. |
| 20 | Performance | p50, p95 and maximum latency, the slowest tests, the budget. |
| 21 | Cost | Tokens and cost by category. A target that reports no usage shows 0, which means *not reported*, not free. |
| 22 | Regression comparison | The [comparison](#comparing-runs) with a baseline, or *"No baseline was supplied"*, and a trend of the target's earlier runs. |
| 23 | Evidence | Every artifact: kind, test, size and SHA-256 ([evidence](#evidence)). |
| 24 | Recommendations | What to change, why, and the action, in priority order. |
| 25 | Remediation priorities | Fixes, coverage and configuration items ordered by severity × confidence, security first. |
| 26 | Appendix | Skills used with versions and hashes, configuration, glossary, how to read the statements in the report. |
| 27 | Raw machine-readable results | Where the full data is (`report.json`) and how it is checked. |

The trend in section 22 is what led up to the run being reported: the earlier runs of the same target (up to 12), then this
run. A report of an older run does not change when newer runs are added.

## Reading a result

A grade is only as wide as what was tested, and the report says so next to it. From the example (the before run, with only
the functional suite and no judge):

```
**acme-assistant: D (functional suite only) (62/100)**

10 of 49 executed tests passed; 18 findings open, the most serious being medium.

**What limits this result**

- Read the grade as partial: only the 'functional' suite was run; 1 area(s) that apply to this target were not fully tested
  (A Basic functional); 2 test(s) were BLOCKED because a prerequisite was missing and do not count as passes. The score
  describes only what was measured.
- Security was not tested; the overall score says nothing about it.
- No independent judge was used: quality criteria that need one were not evaluated.
- 2 tests could not run (BLOCKED) and are excluded from every score; they are gaps in coverage, not failures.
```

* **A category with no test is `N/A`**, shown as such, and does not count for or against the agent. Its weight goes to the
  categories that were measured ([evaluation.md](evaluation.md#scoring)).
* **`BLOCKED` is not `failed`.** A test that could not run (no credential, no judge, no browser, no authorization) says
  nothing about the target. It is listed with its reason and left out of every score.
* **A test stopped by a limit** (`stopped_due_to_cost`, `_timeout`, `_step_limit`) is reported with the limit and is not
  scored either ([configuration.md](configuration.md#limits)).
* **Second-wave tests are evidence, not extra score.** The follow-up variants AgentLab adds after failures
  ([test-case-design.md](test-case-design.md)) are listed in section 11 and 12, and a note says that they are *"reported as
  evidence and not scored, so a weakness is not counted twice"*.
* **A security cap is stated.** When a successful attack lowers the best possible grade, the report says which finding did it
  ([security.md](security.md)).

## Reading a finding

A finding is made for every failed (or timed-out) test and for nothing else ([evaluation.md](evaluation.md#findings)). The
Markdown form, from the example:

```
#### [MEDIUM] No number within ±0.0 of 42.0 (found [17.0, 25.0, 44.0]) [REL-ANSWER-001]
- **Finding:** No number within ±0.0 of 42.0 (found [17.0, 25.0, 44.0]) [REL-ANSWER-001]
- **Evidence:** test REL-ANSWER-001, artifacts sha256-3e4045e26af1, sha256-d27c60afd0bf, …
- **Expected:** The same factual question is answered correctly on every repetition
- **Observed:** no number within ±0.0 of 42.0 (found [17.0, 25.0, 44.0])
- **Impact:** A number in the answer is outside the accepted range or tolerance.
- **Severity:** MEDIUM
- **Confidence:** 0.91 · **Root cause:** unknown (0.25)
- **Reproduction:**
  - 1. Send: 'What is 17 + 25?'
  - Re-run only this test with the same inputs and checks: agentlab test <the same target flags> --baseline 9c266797-… --only REL-ANSWER-001
- **Recommendation:** Check the calculation or the data it is based on; compute numbers in code, not in the model.
- **Observed facts:**
  - [numeric] no number within ±0.0 of 42.0 (found [17.0, 25.0, 44.0])
  - Passed 0/3 repetitions (pass rate 0%, deterministic failure).
- **Inferences:**
  - Root cause (unclear, perhaps unknown, confidence 0.25): checks failed but their pattern does not point to a specific component
```

The statements are kept apart on purpose: **observed facts** are what a check saw, **inferences** are what AgentLab
concluded from them (the likely cause, the severity signals), **judgments** are what a model judge or a human reviewer
thought, and the **recommendation** is advice. Confidence says how sure AgentLab is that the *test result* is right; the
root-cause confidence says how sure it is about the *cause*. In `report.json` a finding also carries its `category`,
`priority`, `status` (`open`, `confirmed`, `false_positive`), the `severity_breakdown` (nine weighted factors, signals,
adjustments and a risk score), `effective_severity` after a review, and its `reviews`.

The reproduction line works as printed: the command replays that one test from the run's own plan, with the same inputs and
checks (add the `--target` file or flags of the agent). It was run for the example and found the same defect.

## Evidence

Everything a report quotes is an artifact stored by content hash (`sha256-…`): the plan, the trace of every test attempt
(what was sent, what came back, tool calls, retrievals, timings), the analysis, the manifest and, for browser tests,
screenshots and page snapshots. Section 23 lists them with their sizes and hashes, and each finding names the ones it rests
on.

* Text is **redacted before it is written**, and a final scrub runs over every field of a report. Redaction is best effort
  ([security.md](security.md#redaction)).
* A report shows **excerpts**, marked as truncated; the complete redacted artifact is referenced by id.
* **Restricted** artifacts (raw traces and the evidence an engine collects, such as screenshots taken while signed in) are
  not embedded in a bundle unless you ask with `--include-sensitive` (the API's `include_sensitive`), and are downloaded
  only with `include_restricted=true`. Treat a bundle made that way like a credential
  ([security.md](security.md#evidence-and-restricted-artifacts)).
* Artifacts can be listed and downloaded through the [API](#through-the-api): `GET /test-runs/{id}/artifacts` and
  `GET /artifacts/{id}`.

## Comparing runs

A comparison answers one question: *did this change make the agent better or worse?* Its rule is never to compare runs that
are not comparable without saying so, so every comparison starts with a verdict on whether the two runs can be compared at
all.

There are two ways to get two runs that can.

**Replay the same tests.** `agentlab test --baseline RUN` re-runs the tests that RUN *started with*: the same ids, inputs and
checks, against the target you point it at. The replay is scored with the same profile, so even the overall scores can be
compared.

```
$ agentlab test --target correct.yaml --baseline 9c266797 --no-judge --report none
…
[ 5/17] - skill_selection 0.0s an existing plan is being executed; skills are not re-selected
      plan wave 1: 23 tests (21 runnable, 2 predicted BLOCKED)
```

A replay does not repeat the follow-up tests that RUN's own failures called for in its later waves, and it adds none of its
own. `--only TEST_ID` with `--baseline` re-runs a single test.

**Run twice and compare.** Two independent runs each discover their target, so a fix can change what is planned (in the
example the fixed agent remembers what it is told, so memory tests and a different scoring profile were planned for it).
The comparison says so instead of hiding it:

```
$ agentlab compare 9c266797 ca2777e4
Regression comparison  A 9c266797  ->  B ca2777e4
Improvement: 13 resolved failures (the overall scores are not comparable: the runs were scored with different profiles).
Compatibility: not comparable - The runs are NOT comparable as scores: scoring_profile (scores use different weights, so
overall scores are not comparable (category results still are)).
  Impact              What differs            A                                    B
  info                target.spec_hash        e03d26bfdf2091bd5c91141766788de39…   26ffd67285c6d3a8e2b4dedf7deb908bf3…
  caveat              plan.hash               a4d53e0bfbcd3929ae038b6d             4d87488afc9897e9da002a22
  blocks comparison   scoring_profile         {'name': 'general', …}               {'name': 'memory_agent', …}
  caveat              skills.memory-testing   None                                 1.0.0

  ! 28 test(s) exist only in A and 5 only in B: the two plans were designed separately. To compare like with like, replay
A's tests against B's target with `agentlab test --baseline 9c266797`.
```

A comparison of a replay with the run it replays is plainer:

```
$ agentlab compare 9c266797 634e0fac
Regression comparison  A 9c266797  ->  B 634e0fac
Improvement: 13 resolved failures, score +38.4.
Compatibility: comparable with caveats - Comparable with 1 caveat: read the differences below before drawing conclusions.
  Impact   What differs       A                                        B
  info     target.spec_hash   e03d26bfdf2091bd5c91141766788de39548b…   26ffd67285c6d3a8e2b4dedf7deb908bf374…
  info     plan.hash          a4d53e0bfbcd3929ae038b6d                 b7bfd9b06567c2d7bd1bdb98
  info     plan.suite         functional                               regression

  ! B replays the tests A started with, so those are compared one to one. 28 test(s) exist only in A and 0 only in B:
follow-up tests are chosen from the results of the run that adds them, so a replay does not repeat the ones A added in its
later waves.
Score 61.6 -> 100 (+38.4) · grade D (functional suite only) -> A (regression replay)

Resolved failures (failed in A, pass in B): 13
  CONV-AMBIGUOUS-001  failed -> passed  Asks for clarification on ambiguous input
  CONV-ARITHMETIC-001  failed -> passed  Answers a simple factual question correctly
  …
```

`compare` takes the baseline first (`A`) and the run to judge second (`B`). `--format md|html|json` (or `--json`) writes the
comparison instead of printing a summary, `-o` names the file.

### What a test did between the two runs

| Kind | Meaning | Counts as |
|---|---|---|
| `new_failure` | Passed in A, fails in B. | regression |
| `resolved` | Failed in A, passes in B. | improvement |
| `still_failing` | Fails in both. | neither |
| `unstable` | Changed, but the test is measured as flaky in at least one run. | neither: a coin flip is not a regression or a fix |
| `lost_coverage` | Ran in A, could not run in B (`BLOCKED`, a missing credential, no judge …). | not a regression: **a test that did not run did not fail** |
| `gained_coverage` | Could not run in A, ran in B. | neither |
| `new_test_failed`, `new_test_passed`, `new_test_not_run` | Only in B. | `new_test_failed` is listed; it is not a regression of an old test |
| `removed` | Only in A. | neither |
| `definition_changed` | Same id, but the test's inputs or checks differ (a skill changed). **Not compared**; both outcomes are listed. | neither |

Findings are compared too (new, resolved, worse, better, unchanged), and so are the categories, the security posture and
the attacks that succeeded, latency (p50 and p95), cost and reliability.

### The verdict

| Verdict | When |
|---|---|
| `regressed` | A test passed in A and fails in B, or a high or critical finding is new or worse, or the security posture is worse. |
| `improved` | A failure was resolved or a finding was resolved, and nothing regressed. |
| `mixed` | Both. |
| `unchanged` | Neither. |
| `inconclusive` | The same run twice, no test in common, or a difference that blocks comparison other than the scoring profile (a different AgentLab major version). |

### Whether the runs can be compared

The verdict is built from the two run manifests ([the bundle](#the-report-bundle)).

| Impact | Differences |
|---|---|
| **Blocks comparison** | A different AgentLab *major* version. A different **scoring profile**: the weights differ, so the *overall scores are not comparable* (category results are, and so is whether each test passed, so the test-level verdict still stands). |
| **Caveat** | A different target name, intensity or minor AgentLab version; a skill used in only one run or changed; different judges, or judging on in only one; different evaluation settings or limits; Docker or a browser available in only one (so tests were `BLOCKED` in one); a different plan or suite **unless B is a replay of A**; fewer than 5 tests in common; tests that exist in only one run. |
| **Info** | A different target version or definition (that is the point of a regression run), a patch-level AgentLab difference, and, for a replay, its new plan id and the `regression` suite. |

The same comparison is written into a report as section 22 when you give `--baseline` (or when the run was started with one).
`agentlab compare --fail-on-regression` turns it into a gate for CI: exit **1** when B regressed (or the result is mixed), **3**
when the runs cannot be compared, **0** otherwise.

```
$ agentlab compare 634e0fac 9c266797 --fail-on-regression; echo $?     # the fix undone
1
$ agentlab compare 9c266797 9c266797 --fail-on-regression; echo $?      # the same run twice
3
```

## Human review

A person can confirm, reject or re-rate what AgentLab found. **The evaluation is never changed.** A review is stored as a new
row next to the original (who, when, why, the value before and the value after), and a report shows both.

| Decision | On | What it says | Effect in the reviewed view |
|---|---|---|---|
| `approve` | a finding, or a result | "I confirm this." | A finding becomes `confirmed`. On a result it changes nothing. |
| `false_positive` | a failed result, or a finding | "This was not a defect." | The result counts as passed (score 1.0), and the finding it produced is rejected. Refused for a test that did not fail. |
| `false_negative` | a passed result | "This should have failed." | The result counts as failed (score 0) with severity medium unless `--severity` says otherwise. |
| `override_score` | a result | "The score should be this." (`--score`, 0 to 1) | The result takes that score. |
| `change_severity` | a result or a finding | "This is more (or less) serious." (`--severity`) | It takes that severity. |
| `comment` | a result or a finding | A note for the next reader. | None. |

`--reviewer` (who) is always required. `--reason` is required for `false_positive`, `false_negative`, `override_score` and
`change_severity`, so the next reader can follow the decision. A later review of the same field wins.

```
$ agentlab review result 9c266797 CONV-EMPTY-INPUT-001 --decision false_positive --reviewer "Dana Whitfield" \
    --reason "The reply is an apology; the stack-trace check matched an unrelated word."
recorded false_positive on result by Dana Whitfield (review 4d614b91); the original evaluation is unchanged.
Run `agentlab report` to write a report that shows it next to the original.

$ agentlab review finding 9c266797 REL-ANSWER-001 --decision change_severity --severity high --reviewer "Dana Whitfield" \
    --reason "Wrong arithmetic in a billing assistant is a customer-visible error."
recorded change_severity on finding by Dana Whitfield (review dd36489f); the original evaluation is unchanged.
```

`agentlab review list RUN` lists the reviews oldest first with the values before and after.

A report written after a review (`agentlab report`, a new version) says so at the top, keeps the machine result, and adds a
second, clearly labelled one:

```
- **Human review:** this report includes reviewer decisions; original results are shown unchanged.
- **Note:** After human review the score is 56.5 (machine score 61.6); both are shown.

**After human review:** 56 / 100, grade F (1 high-severity failure; functional suite only) (machine score 62).

| When (UTC) | Reviewer | About | Decision | Original | Reviewed | Reason / comment |
| 2026-10-06 21:23 | Dana Whitfield | result CONV-EMPTY-INPUT-001 | false_positive | status failed · score 0.50 · severity medium | status passed · score 1.00 | The reply is an apology; … |
| 2026-10-06 21:23 | Dana Whitfield | finding REL-ANSWER-001 | change_severity | status open · severity medium | severity high | Wrong arithmetic in a billing assistant … |
```

In the test table the result reads *failed → passed (reviewed)*, and the finding shows *Severity: HIGH (machine: MEDIUM)* with
the reviewer's name. The reviewed score is the same scoring profile applied to the reviewed values: an opinion is never
presented as a measurement. In this example the false positive raised the score and the new high-severity failure lowered the
best grade, so the reviewed score is lower.

What review is not:

* **A review belongs to one run.** A later run is not reviewed, even if it fails the same test.
* **The reviewer's name is typed in and not verified.** AgentLab has one shared API token and no accounts
  ([security.md](security.md#web-interface-and-api)). Do not read a name in a report as an identity.
* **There is no approval workflow**: no sign-off, no second reviewer, no lock.

## Through the API

The reference is the OpenAPI document at `/docs` of a running `agentlab serve`. The endpoints that belong to this page:

| Method and path | What it does |
|---|---|
| `GET /test-runs/{id}/reports` | The reports (versions) of a run, newest first, with their formats and sizes. |
| `POST /test-runs/{id}/reports` | Write a report: `{"formats": ["html"], "include_sensitive": false, "baseline_run_id": null}`. **201**; the response is the new report version. |
| `GET /reports/{id}` | One report version. |
| `POST /reports/{id}/export` | `{"format": "pdf"}`: the file in that format, written as a **new version** when that version does not have it (`created_new_version: true`). |
| `GET /reports/{id}/files/{format}` | Download one file (`json`, `md`, `html` or `pdf`). |
| `POST /reports/{id}/view-link` | A signed link, `{"url": "/view/…", "expires_in": 300}`, that shows the HTML report. It works for five minutes and until the server restarts, and the page is served with a sandboxing Content-Security-Policy. A changed token is a 404. |
| `GET /comparisons?run_a=…&run_b=…` | The comparison as JSON (schema `agentlab.comparison`, version 1). |
| `GET /test-runs/{id}/artifacts?kind=…` | The evidence of a run. |
| `GET /artifacts/{id}` | One artifact (`include_restricted=true` for restricted ones, `inline=true` to show a safe type). |
| `POST /test-runs/{id}/reviews`, `GET /test-runs/{id}/reviews` | Review a result or a finding ([the same decisions](#human-review)); list the reviews. |

A review is a JSON body (`subject` is `result` or `finding`; `subject_id` is a test id or a finding id; the rules about
`reason`, `score` and `severity` are the ones above, and a missing reason is a 422 naming the decision):

```
$ curl -s -X POST http://127.0.0.1:8000/test-runs/9c266797/reviews -H 'content-type: application/json' \
    -d '{"subject":"finding","subject_id":"REL-ANSWER-002","decision":"approve","reviewer":"Dana Whitfield"}'
{"decision":"approve","subject_type":"finding","subject_label":"REL-ANSWER-002","reviewer":"Dana Whitfield",
 "original":{"status":"open","severity":"medium"},"reviewed":{"status":"confirmed"}, …}
```

```
$ curl -s -X POST http://127.0.0.1:8000/test-runs/9c266797/reports \
    -H 'content-type: application/json' -d '{"formats":["html"]}' -o report.json -w '%{http_code}\n'
201
$ curl -sI http://127.0.0.1:8000/reports/<report id>/files/html
HTTP/1.1 200 OK
content-disposition: inline; filename="agentlab-report-9c266797-v4.html"
cache-control: no-store
content-security-policy: sandbox allow-scripts; default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; …
```

With an API token configured, every request carries `Authorization: Bearer TOKEN` ([security.md](security.md#web-interface-and-api)).

## In the web interface

* **A run's *Reports* tab** and the **Reports** page list the versions of a run's reports. From there you read one (in a
  sandboxed frame, through a signed link), download a file, or generate a report in another format, with or without a
  baseline run and restricted evidence.
* **Compare** takes two runs and shows the verdict, the compatibility, the changed tests by kind, the categories, latency,
  cost, reliability and security side by side.
* **A run's *Findings* tab** shows each finding with its evidence and the review form and the reviews it already has.

## Configuration

| Key | Default | Meaning |
|---|---|---|
| `reporting.formats` | `[json, md, html, pdf]` | Written after every run (`--report` overrides it). `[]` writes none. |
| `reporting.include_sensitive_artifacts` | `false` | Embed restricted evidence in the files. |
| `storage.reports_dir` | `.agentlab/reports` | Where bundles are written. |
| `AGENTLAB_PDF_FONT_DIR` | unset | A folder with a TrueType font for PDF text the built-in fonts cannot show. |

([configuration.md](configuration.md#reporting)).

## What reports do not do

* **No retention and no delete.** AgentLab keeps its runs, artifacts and reports until you remove them; there is no command
  that deletes a run ([security.md](security.md#evidence-and-restricted-artifacts)).
* **No signature.** Checksums detect a changed file, not who changed it.
* **No identity for reviewers.** See [human review](#human-review).
* **No verdict on what was not tested.** A grade is partial when the suite is, and a run that tested nothing has no grade.
* **No claim that the absence of findings is safety.** Security tests are canary based and bounded by the intensity; the
  report says so every time ([security.md](security.md)).
* **Redaction is best effort.** Read a report before you share it, and use `--include-sensitive` only for bundles that stay
  inside your organisation.

## Tested by

`tests/integration/test_reports.py` (all 27 sections in every format; the four formats are renderings of the same data; the
HTML is one self-contained inert file; blocked is never failed; a flawed agent never gets an unqualified good grade; the
finding shape; hostile text is inert in HTML, Markdown and PDF; secret-shaped values never reach a report; every report is a
new version; checksums catch a changed, missing or added file; restricted evidence is embedded only when asked for; the PDF
is valid, bookmarked and reproducible; reviews sit beside the original and are validated; the comparison kinds, verdicts and
compatibility rules above, including a replay and the trend), `tests/unit/test_manifest.py` (what makes two manifests
comparable), `tests/integration/test_cli_markup.py` (hostile text on the terminal), `tests/api/` (the report, comparison,
artifact and review endpoints, signed links and restricted downloads) and acceptance scenario 12 in
`tests/e2e/test_acceptance.py` (the same suite on two versions of one agent reveals the regression and the fix, and the
comparison is a deliverable in every format).
