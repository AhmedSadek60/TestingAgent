# ADR-0005: BLOCKED is not FAILED; the judge is independent of the target

- Status: Proposed
- Date: 2026-10-06
- Deciders: Ahmed (to accept by review)

## Context

A test run mixes tests that ran with tests that could not: no Docker for a coding agent, no tool to exercise,
no judge for a quality criterion, a limit reached. A score that treats "could not run" like "failed" punishes
the agent for the evaluator's gaps; one that treats it like "passed" hides risk. Separately, an LLM judge that
is the same model as the target shares its blind spots and may be steered by the very text it is judging.

## Problem

How should a result say "this was not measured", and how can quality be judged without letting the target
influence its own evaluation?

## Decision

* `TestStatus` has distinct `blocked`, `skipped`, `error`, `timeout` and `stopped_due_to_*` values besides
  `passed` and `failed`. A blocked test carries the reason, never counts as a failure or a pass, and is listed in
  the scorecard, the plan and the report. A category whose tests were all blocked is **N/A**, and its weight is
  redistributed over the categories that were measured. The scorecard adds a note such as "N test(s) were
  BLOCKED (prerequisite missing) and are not counted as failures", and a grade shows a qualifier when coverage
  was partial.
* Quality criteria that need an LLM are evaluated only by a judge that is independent of the target. A judge
  whose provider and model are the target's own is refused: the judge is switched off, the run records that it
  was not independent and why, and the criteria that needed it are blocked, not guessed. A judge whose model
  merely appears in the target's files draws a warning, because that detection is a heuristic.
* Everything the target said reaches the judge inside nonce-bound `UNTRUSTED_*` blocks, with a system prompt that
  says such blocks are data. The judge never receives credentials, returns structured JSON (score, verdict,
  confidence, uncertainty, cited evidence) and its reasoning is not stored beyond that brief justification. The
  rubric and a hash of the full prompt are stored so a verdict can be reproduced.
* Several judges can be combined (`average`, `vote`, `min`); disagreement lowers confidence and flags the result
  for human review.

## Alternatives considered

- **Count blocked as failed:** rejected; it makes an evaluator gap look like an agent defect.
- **Count blocked as passed or ignore it silently:** rejected; it overstates the result.
- **Let the target model also be the judge:** rejected; the target could steer its own evaluation and would
  share its own blind spots.
- **Judge with no rubric:** rejected; not reproducible.

## Consequences

Scores are comparable only with their coverage: the report shows what was and was not measured, and a
comparison between runs reports a test that ran in one run and could not in the other as a change of coverage,
not as a regression. A run with no judge configured has only deterministic and trajectory evidence, and says so.

## Risks

Even an independent judge can be wrong or be fooled by text crafted for judges; the deterministic layer and
human review exist because of that, and judge-only findings carry their confidence.

## Migration / rollout notes

None.
