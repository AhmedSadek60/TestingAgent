import { Link } from "react-router-dom";
import { Radar } from "../components/Radar";
import { Async, BarRow, Card, Empty, GradeBadge, KeyValue, Notice, ScoreRing } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { formatNumber, formatPercent, titleCase } from "../lib/format";
import { splitGrade, statusLabel } from "../lib/labels";
import { isActive } from "../lib/runs";
import { useSession } from "../session";
import { useRun } from "./RunLayout";

/** The score by category, how sure the evaluation is, and what limits it. */
export function Scorecard() {
  const { run } = useRun();
  const { api } = useSession();
  const card = useAsync((signal) => api.scorecard(run.id, signal), [api, run.id], { pollMs: 4000, until: () => !isActive(run.status) });

  return (
    <Async state={card} loading="Loading the scorecard…">
      {(sc) => {
        const categories = [...sc.categories].sort((a, b) => b.weight - a.weight || a.category.localeCompare(b.category));
        const scored = categories.filter((c) => c.score !== null);
        const counts = Object.entries(sc.counts);
        return (
          <div className="stack" style={{ gap: 16 }}>
            {sc.overall === null && (
              <Notice tone="warn" title="This run has no overall score">
                {isActive(run.status) ? "The score is computed when the run has finished." : "Not enough tests ran to score it honestly, so no number is given."}
              </Notice>
            )}
            {sc.security_cap_applied && (
              <Notice tone="error" title="The overall score is capped by a security problem">
                {sc.cap_reason ?? "A serious security finding limits the overall score."}
                {sc.raw_overall !== null && ` Without the cap it would be ${Math.round(sc.raw_overall)}.`}
              </Notice>
            )}
            <div className="grid two">
              <Card title="Overall">
                <div className="row" style={{ gap: 24, alignItems: "flex-start" }}>
                  <ScoreRing score={sc.overall} grade={sc.grade} size={170} />
                  <div className="stack tight grow">
                    <div>
                      Grade <GradeBadge grade={sc.grade} />
                    </div>
                    {splitGrade(sc.grade).notes.length > 0 && (
                      <div className="small">
                        The grade is qualified: {splitGrade(sc.grade).notes.join("; ")}.
                      </div>
                    )}
                    <div className="muted">Confidence in the number: {formatPercent(sc.overall_confidence)}</div>
                    <div className="muted small">
                      Profile <strong>{sc.profile}</strong>: {sc.profile_description}
                    </div>
                    {counts.length > 0 && (
                      <div className="row small">
                        {counts.map(([key, value]) => (
                          <span key={key} className="tag">
                            {statusLabel(key)} {formatNumber(value)}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              </Card>
              <Card title="Shape of the score">
                {scored.length >= 3 ? <Radar items={categories.map((c) => ({ label: c.category, value: c.score }))} /> : <p className="muted">Fewer than three categories were scored, so there is no shape to draw.</p>}
              </Card>
            </div>

            <Card title="Categories">
              {categories.length === 0 ? (
                <Empty title="No category was scored" />
              ) : (
                <div className="stack">
                  {categories.map((c) => (
                    <div key={c.category}>
                      <BarRow label={titleCase(c.category)} value={c.applicable ? c.score : null} note={c.applicable ? c.note : (c.note ?? "not applicable to this agent")} confidence={c.confidence} />
                      <div className="muted small" style={{ marginLeft: 0, paddingLeft: 0 }}>
                        weight {formatPercent(c.weight)} · {c.passed} of {c.tests} test{c.tests === 1 ? "" : "s"} passed · confidence {formatPercent(c.confidence)}
                        {c.note && c.applicable ? ` · ${c.note}` : ""}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Card>

            {(sc.qualifiers.length > 0 || sc.notes.length > 0) && (
              <Card title="What limits this score">
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {[...sc.qualifiers, ...sc.notes].map((text, i) => (
                    <li key={i}>{text}</li>
                  ))}
                </ul>
              </Card>
            )}

            <Card title="Scoring profile">
              <KeyValue items={[["Profile", sc.profile], ["Description", sc.profile_description], ["Weights", Object.entries(sc.weights).map(([k, v]) => `${k} ${formatPercent(v)}`).join(" · ") || "none"]]} />
              <p className="muted small" style={{ marginBottom: 0 }}>
                Blocked and skipped tests are not counted as failures, and categories that did not run are shown as such rather than scored zero. Reviews are never folded into this machine score: see{" "}
                <Link to={`/runs/${run.id}/reports`}>the report</Link> for the reviewed scorecard.
              </p>
            </Card>
          </div>
        );
      }}
    </Async>
  );
}
