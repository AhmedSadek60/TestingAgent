---
name: report-generation
description: How results become a report: executive summary, scores, findings with evidence, coverage and limitations in HTML, Markdown, JSON and PDF, with observed facts kept apart from inferences and recommendations.
---

# Report generation

Skill `report-generation` v1.0.0 · kind `meta` · taxonomy n/a · default risk class `safe` · test-id prefix `REP`

## Purpose

Give humans a faithful account: what was tested, what was observed, what it means and what was *not* tested.

## Applicability

Not selected automatically: it is used by an explicit suite or request (for example `agentlab test --suite regression`).
The plan always states why a skill was selected or skipped.

## Prerequisites

- PDF export needs a browser or the bundled PDF writer.

## Methodology

Reports are rendered from the stored run (never from live state). Each finding separates observed fact, inference, judgment and recommendation, carries severity and root cause with confidence, and links to evidence. BLOCKED, SKIPPED and STOPPED tests are reported separately from failures. A leaky or unsafe agent never receives an unqualified excellent rating; limitations and non-covered capabilities are always included.

## Test generation

No tests. The report skill defines sections and wording rules used by the report renderers.

Generator: none (this skill produces no tests).

## Execution

Renders JSON (canonical), Markdown, HTML (self-contained) and PDF, writes a checksummed bundle and records the report version.

## Evaluation rules

- Executive summary states coverage and caveats first.
- No claim without evidence; inference is labelled.
- Secrets are redacted in every format.

## Severity guidance

Default severity on failure: **info**.

- Not applicable.

## Evidence requirements

- run id, plan hash, tool and skill versions
- checksums of all report files

## Metrics

- (none)

## Limitations

- PDF layout is simpler than HTML.

## References

- https://www.nist.gov/itl/ai-risk-management-framework
- https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents

These sources informed the methodology; no third-party content or code was imported into this skill.
