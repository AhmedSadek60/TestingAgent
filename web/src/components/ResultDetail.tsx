import { Link } from "react-router-dom";
import type { AttemptResult, Review, TestResult } from "../api/types";
import { formatCost, formatLatency, formatNumber, formatPercent, formatTime } from "../lib/format";
import { rootCauseLabel } from "../lib/labels";
import { outcomeLine } from "../lib/results";
import { JsonView, RedactedText, Untrusted } from "./JsonView";
import { ReviewForm, ReviewList } from "./Review";
import { Badge, Card, KeyValue, Notice, SeverityBadge, StatusBadge } from "./ui";

function Attempt({ result, attempt, runId }: { result: TestResult; attempt: AttemptResult; runId: string }) {
  const failed = attempt.status !== "passed";
  const checks = attempt.assertions;
  return (
    <details open={failed && result.attempts.length <= 3} className="card" style={{ padding: 0 }}>
      <summary style={{ padding: "10px 14px", cursor: "pointer" }}>
        <span className="row" style={{ display: "inline-flex" }}>
          <strong>Attempt {attempt.attempt}</strong>
          <StatusBadge status={attempt.status} />
          <span className="muted small">
            {formatLatency(attempt.latency_ms)} · {formatNumber(attempt.tokens)} tokens · {attempt.steps} step{attempt.steps === 1 ? "" : "s"}
          </span>
        </span>
      </summary>
      <div className="stack" style={{ padding: "0 14px 14px" }}>
        {attempt.error && (
          <Notice tone="error" title={attempt.error_kind ? `Error: ${attempt.error_kind.replace(/_/g, " ").toLowerCase()}` : "Error"}>
            <RedactedText text={attempt.error} />
          </Notice>
        )}
        {checks.length > 0 && (
          <div className="table-wrap">
            <table className="table">
              <caption className="sr-only">Deterministic checks of attempt {attempt.attempt}</caption>
              <thead>
                <tr>
                  <th scope="col">Check</th>
                  <th scope="col">Result</th>
                  <th scope="col">What was observed</th>
                </tr>
              </thead>
              <tbody>
                {checks.map((c, i) => (
                  <tr key={`${c.type}-${i}`}>
                    <td className="mono">
                      {c.type}
                      {!c.required && <div className="muted small">advisory</div>}
                    </td>
                    <td>
                      {c.evaluator_error ? <Badge tone="warn">evaluator error</Badge> : c.passed ? <Badge tone="good">passed</Badge> : <Badge tone="bad">failed</Badge>}
                      {c.severity && !c.passed && <> <SeverityBadge severity={c.severity} /></>}
                    </td>
                    <td>
                      <RedactedText text={c.message} />
                      {Object.keys(c.evidence).length > 0 && (
                        <details>
                          <summary className="small muted" style={{ cursor: "pointer" }}>
                            evidence
                          </summary>
                          <JsonView value={c.evidence} />
                        </details>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {attempt.judge.length > 0 && (
          <div className="stack tight">
            <h4>Independent judge</h4>
            {attempt.judge.map((j) => (
              <div key={j.metric} className="card" style={{ padding: 10 }}>
                <div className="row between">
                  <strong>{j.metric}</strong>
                  <span>
                    {j.uncertain ? <Badge tone="warn">uncertain</Badge> : j.passed ? <Badge tone="good">passed</Badge> : <Badge tone="bad">failed</Badge>}{" "}
                    <span className="muted small">
                      score {formatPercent(j.score)} · confidence {formatPercent(j.confidence)} · {j.strategy}
                      {j.votes.length > 1 ? ` · agreement ${formatPercent(j.agreement)}` : ""}
                    </span>
                  </span>
                </div>
                <p className="muted small" style={{ margin: "4px 0 0" }}>
                  Rubric: {j.rubric}
                </p>
                {j.error && <p className="small" style={{ color: "var(--bad)" }}>{j.error}</p>}
              </div>
            ))}
            <p className="muted small" style={{ margin: 0 }}>
              A judge opinion is a judgment, not an observation. Where the judge was uncertain, the test is not counted as a pass or a fail.
            </p>
          </div>
        )}
        {attempt.outputs.length > 0 && (
          <div className="stack tight">
            <h4>What the agent said</h4>
            {attempt.outputs.map((text, i) => (
              <Untrusted key={i} text={text} />
            ))}
          </div>
        )}
        {Object.keys(attempt.trajectory).length > 0 && (
          <details>
            <summary className="small muted" style={{ cursor: "pointer" }}>
              Trajectory metrics
            </summary>
            <JsonView value={attempt.trajectory} />
          </details>
        )}
        {attempt.trace_id && (
          <div>
            <Link to={`/runs/${runId}/traces?trace=${encodeURIComponent(attempt.trace_id)}`}>Open the trace of this attempt</Link>
          </div>
        )}
      </div>
    </details>
  );
}

/** Everything known about one test result, with the means to review it. */
export function ResultDetail({ runId, result, reviews, onReviewed }: { runId: string; result: TestResult; reviews: Review[]; onReviewed: () => void }) {
  const mine = reviews.filter((r) => r.subject_type === "result" && (r.subject_id === result.id || r.subject_label === result.test_id));
  const rel = result.reliability;
  return (
    <>
      <div className="row">
        <StatusBadge status={result.status} />
        <SeverityBadge severity={result.severity} />
        {result.review && <Badge tone="info">reviewed</Badge>}
        <span className="muted mono small">{result.test_id}</span>
      </div>
      <p style={{ margin: 0 }}>
        <RedactedText text={outcomeLine(result)} />
      </p>
      {result.status === "blocked" && (
        <Notice tone="info" title="Blocked is not failed">
          A prerequisite was missing, so the test did not run and the agent was not judged on it. It does not count against the score.
        </Notice>
      )}
      <Card title="Summary">
        <KeyValue
          items={[
            ["Category", `${result.category}${result.score_category !== result.category ? ` (scored under ${result.score_category})` : ""}`],
            ["Score", result.status === "blocked" || result.status === "skipped" ? "not scored" : formatPercent(result.score)],
            ["Confidence", formatPercent(result.confidence)],
            ["Root cause", result.root_cause ? `${rootCauseLabel(result.root_cause)}${result.root_cause_confidence !== null ? ` (${formatPercent(result.root_cause_confidence)} sure)` : ""}` : "not determined"],
            ["Error kind", result.error_kind ? result.error_kind.replace(/_/g, " ").toLowerCase() : null],
            ["Latency", formatLatency(result.latency_ms)],
            ["Tokens and cost", `${formatNumber(result.tokens)} tokens · ${formatCost(result.cost_usd)}`],
            ["Started", formatTime(result.started_at)],
            ["Finished", formatTime(result.finished_at)],
          ]}
        />
      </Card>
      {rel && rel.repetitions > 1 && (
        <Card title="Reliability across repetitions">
          <KeyValue
            items={[
              ["Passed", `${rel.passes} of ${rel.repetitions} (${formatPercent(rel.pass_rate)})`],
              ["Verdict", rel.flaky ? <Badge tone="warn">flaky: the outcome changed between repetitions</Badge> : rel.deterministic_failure ? <Badge tone="bad">fails every time</Badge> : <Badge tone="good">stable</Badge>],
              ["Timeouts and errors", `${formatPercent(rel.timeout_rate)} timeouts · ${formatPercent(rel.error_rate)} errors`],
              ["Latency", `median ${formatLatency(rel.latency_p50_ms)} · 95th percentile ${formatLatency(rel.latency_p95_ms)}`],
              ["Output variance", rel.output_variance.toFixed(2)],
            ]}
          />
        </Card>
      )}
      {result.attempts.length > 0 ? (
        <div className="stack">
          <h3>Attempts</h3>
          {result.attempts.map((a) => (
            <Attempt key={a.attempt} result={result} attempt={a} runId={runId} />
          ))}
        </div>
      ) : (
        <p className="muted">This test has no attempts: it did not run.</p>
      )}
      {result.evidence.length > 0 && (
        <Card title={`Evidence (${result.evidence.length})`}>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {result.evidence.map((e) => (
              <li key={e} className="mono small">
                <RedactedText text={e} />
              </li>
            ))}
          </ul>
        </Card>
      )}
      <Card title="Human review">
        <div className="stack">
          <ReviewList reviews={mine} />
          <ReviewForm key={`${result.id}:${result.status}`} runId={runId} subject="result" subjectId={result.id} status={result.status} onDone={onReviewed} />
        </div>
      </Card>
    </>
  );
}
