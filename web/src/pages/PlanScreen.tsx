import { useCallback, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { describeError } from "../api/errors";
import { isTerminal } from "../api/types";
import { Checkbox } from "../components/controls";
import { Icon } from "../components/Icon";
import { PlanView } from "../components/PlanView";
import { Async, Card, Loading, Notice, PageHeader, useToast } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { planTotals } from "../lib/plan";
import { useSession } from "../session";
import { useRun } from "./RunLayout";

/** A designed plan, on its own: look at it, switch tests off, and run exactly what was approved. */
export function PlanScreen() {
  const { runId = "" } = useParams();
  const { api, project } = useSession();
  const navigate = useNavigate();
  const toast = useToast();
  const out = useAsync((signal) => api.plan(runId, signal), [api, runId], { pollMs: 1500, until: (o) => isTerminal(o.status) });
  const [deselected, setDeselected] = useState<ReadonlySet<string>>(new Set());
  const [judge, setJudge] = useState(true);
  const [secondWave, setSecondWave] = useState(true);
  const [busy, setBusy] = useState(false);

  const toggle = useCallback((ids: string[], on: boolean) => {
    setDeselected((old) => {
      const next = new Set(old);
      for (const id of ids) {
        if (on) next.delete(id);
        else next.add(id);
      }
      return next;
    });
  }, []);

  const start = async () => {
    const plan = out.data?.plan;
    if (!plan) return;
    setBusy(true);
    try {
      const accepted = await api.startRun({
        project,
        plan_id: runId,
        deselect: [...deselected],
        options: { suite: plan.suite, intensity: plan.intensity, judge, second_wave: secondWave },
      });
      toast.push("good", "The run was queued.");
      navigate(`/runs/${accepted.run_id}`);
    } catch (e) {
      toast.push("error", describeError(e));
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader
        title={out.data?.profile?.target_name ? `Test plan for ${out.data.profile.target_name}` : "Test plan"}
        subtitle="What would be tested, why, what each test needs, what cannot run and what it should cost. Nothing has been run against the agent yet, beyond harmless discovery probes."
        actions={
          <Link to="/runs" className="btn">
            <Icon name="list" size={16} /> All runs
          </Link>
        }
      />
      <Async state={out} loading="Loading the plan…">
        {(data) => {
          if (!isTerminal(data.status)) return <Loading label="Designing the plan: reading the target, choosing skills, classifying risk…" />;
          if (data.status !== "completed" || !data.plan) {
            return (
              <Notice tone="error" title={`The plan was not designed (${data.status})`}>
                {data.error ? JSON.stringify(data.error) : "Open the run list for details."}
              </Notice>
            );
          }
          const totals = planTotals(data.plan, deselected);
          return (
            <PlanView
              out={data}
              deselected={deselected}
              onToggle={toggle}
              extra={
                <Card title="Run this plan">
                  <div className="stack">
                    <Checkbox checked={judge} onChange={setJudge}>
                      Use the independent judge for criteria that deterministic checks cannot decide
                    </Checkbox>
                    <Checkbox checked={secondWave} onChange={setSecondWave}>
                      Add follow-up tests for what the first wave finds
                    </Checkbox>
                    <div className="row">
                      <button type="button" className="btn primary" disabled={busy || totals.selected === 0} onClick={start}>
                        <Icon name="play" size={16} /> Run {totals.selected} test{totals.selected === 1 ? "" : "s"}
                      </button>
                      <span className="muted small">The plan runs exactly as shown, minus the tests you switched off.</span>
                    </div>
                  </div>
                </Card>
              }
            />
          );
        }}
      </Async>
    </>
  );
}

/** The plan a run executed (read only). */
export function RunPlan() {
  const { run } = useRun();
  const { api } = useSession();
  const out = useAsync((signal) => api.runPlan(run.id, signal), [api, run.id]);
  const none = useState<ReadonlySet<string>>(() => new Set())[0];
  return (
    <Async state={out} loading="Loading the plan…">
      {(data) => (data.plan ? <PlanView out={data} deselected={none} /> : <Notice tone="info" title="No plan was recorded for this run">{data.error ? JSON.stringify(data.error) : "The run ended before a plan was designed."}</Notice>)}
    </Async>
  );
}
