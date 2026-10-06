import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import type { Api } from "../api/endpoints";
import { describeError } from "../api/errors";
import { isTerminal, type PlanOut } from "../api/types";
import { Icon } from "../components/Icon";
import { PlanView } from "../components/PlanView";
import { Card, ErrorNote, KeyValue, Loading, Notice, PageHeader, Stat, useToast } from "../components/ui";
import { formatCost, formatDuration } from "../lib/format";
import { planTotals } from "../lib/plan";
import {
  firstProblemStep,
  initialWizard,
  planRequest,
  planSignature,
  runRequest,
  STEPS,
  summarise,
  validateStep,
  type StepId,
  type WizardState,
} from "../lib/wizard";
import { useSession } from "../session";
import { CredentialsStep, DetailsStep, ModelsStep, ModeStep, ObjectiveStep, SourceStep } from "./wizard/steps";
import type { StepProps } from "./wizard/shared";

type Design =
  | { phase: "idle" }
  | { phase: "designing"; signature: string }
  | { phase: "ready"; signature: string; out: PlanOut }
  | { phase: "failed"; signature: string; error: string };

const sleep = (ms: number, signal: AbortSignal) =>
  new Promise<void>((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener("abort", () => (clearTimeout(timer), resolve()), { once: true });
  });

/** Designing a plan is a background job on the server: ask for it, then wait until it is ready. */
function usePlanDesign(api: Api, project: string, state: WizardState) {
  const [design, setDesign] = useState<Design>({ phase: "idle" });
  const abort = useRef<AbortController | null>(null);
  useEffect(() => () => abort.current?.abort(), []);

  const begin = useCallback(async () => {
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;
    const signature = planSignature(state, project);
    setDesign({ phase: "designing", signature });
    try {
      let out = await api.createPlan(planRequest(state, project, 45));
      while (!isTerminal(out.status) && !controller.signal.aborted) {
        await sleep(1500, controller.signal);
        if (controller.signal.aborted) return;
        out = await api.plan(out.run_id, controller.signal);
      }
      if (controller.signal.aborted) return;
      if (out.status === "completed" && out.plan) setDesign({ phase: "ready", signature, out });
      else setDesign({ phase: "failed", signature, error: planFailure(out) });
    } catch (e) {
      if (!controller.signal.aborted) setDesign({ phase: "failed", signature, error: describeError(e) });
    }
  }, [api, project, state]);

  return { design, begin };
}

function planFailure(out: PlanOut): string {
  const e = out.error as { message?: string } | null;
  return e?.message ?? `The plan was not designed (the job ${out.status}).`;
}

function Stepper({ step, reachable, onGo }: { step: number; reachable: (index: number) => boolean; onGo: (index: number) => void }) {
  return (
    <nav className="stepper" aria-label="Steps of the new evaluation">
      <ol>
        {STEPS.map((s, index) => (
          <li key={s.id}>
            <button type="button" className={`step${index === step ? " current" : index < step ? " done" : ""}`} aria-current={index === step ? "step" : undefined} disabled={!reachable(index)} onClick={() => onGo(index)}>
              <span className="n" aria-hidden="true">
                {index < step ? "✓" : index + 1}
              </span>
              {s.label}
            </button>
          </li>
        ))}
      </ol>
    </nav>
  );
}

/** The eight-step wizard: describe the target, say what you want, review the plan, then run it. */
export function NewEvaluation() {
  const { api, project } = useSession();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const toast = useToast();

  const [state, setState] = useState<WizardState>(() => {
    const target = params.get("target");
    const baseline = params.get("baseline");
    const mode = params.get("mode");
    return {
      ...initialWizard(),
      ...(target ? { source: "existing" as const, existingTargetId: target } : {}),
      ...(baseline ? { mode: "regression" as const, baselineRunId: baseline } : {}),
      ...(mode === "discovery" || mode === "functional" || mode === "security" || mode === "browser" || mode === "full" ? { mode } : {}),
    };
  });
  const [step, setStep] = useState(() => (params.get("baseline") ? 4 : params.get("target") ? 1 : 0));
  const [tried, setTried] = useState<Set<StepId>>(new Set());
  const [deselected, setDeselected] = useState<ReadonlySet<string>>(new Set());
  const [starting, setStarting] = useState(false);
  const patch = useCallback((changes: Partial<WizardState>) => setState((s) => ({ ...s, ...changes })), []);

  const { design, begin } = usePlanDesign(api, project, state);
  const current = STEPS[step].id;
  const problems = validateStep(current, state);
  const ready = firstProblemStep(state) === null;
  const signature = ready ? planSignature(state, project) : null;
  const fresh = design.phase !== "idle" && signature !== null && design.signature === signature;

  // entering the plan step designs the plan; changed answers make an old plan stale and it is designed again
  useEffect(() => {
    if (current === "plan" && ready && !fresh && design.phase !== "designing") void begin();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current, signature]);
  useEffect(() => setDeselected(new Set()), [design.phase === "ready" ? design.out.run_id : null]);

  const reachable = (index: number): boolean => {
    if (index <= step) return true;
    for (let i = 0; i < Math.min(index, 6); i += 1) if (Object.keys(validateStep(STEPS[i].id, state)).length > 0) return false;
    return index <= 6 || (design.phase === "ready" && fresh);
  };
  const go = (index: number) => {
    if (index > step) setTried((t) => new Set(t).add(current));
    setStep(index);
    window.scrollTo?.({ top: 0 });
  };
  const next = () => {
    setTried((t) => new Set(t).add(current));
    if (Object.keys(problems).length > 0) return;
    if (step < STEPS.length - 1) go(step + 1);
  };

  const toggle = useCallback((ids: string[], on: boolean) => {
    setDeselected((old) => {
      const out = new Set(old);
      for (const id of ids) {
        if (on) out.delete(id);
        else out.add(id);
      }
      return out;
    });
  }, []);

  const start = async () => {
    if (design.phase !== "ready") return;
    setStarting(true);
    try {
      const accepted = await api.startRun(runRequest(state, project, design.out.run_id, [...deselected]));
      toast.push("good", "The run was queued.");
      navigate(`/runs/${accepted.run_id}`);
    } catch (e) {
      toast.push("error", describeError(e));
      setStarting(false);
    }
  };

  const stepProps: StepProps = { state, patch, problems, showProblems: tried.has(current) };
  const totals = useMemo(() => (design.phase === "ready" && design.out.plan ? planTotals(design.out.plan, deselected) : null), [design, deselected]);

  return (
    <>
      <PageHeader title="New evaluation" subtitle="Tell AgentLab what to evaluate, review the plan it designs, and run it. Nothing is sent to the agent, beyond harmless discovery probes, until you start the run." />
      <Stepper step={step} reachable={reachable} onGo={go} />
      <Card title={`Step ${step + 1} of ${STEPS.length}: ${STEPS[step].label}`}>
        {current === "source" && <SourceStep {...stepProps} />}
        {current === "details" && <DetailsStep {...stepProps} />}
        {current === "credentials" && <CredentialsStep {...stepProps} />}
        {current === "objective" && <ObjectiveStep {...stepProps} />}
        {current === "mode" && <ModeStep {...stepProps} />}
        {current === "models" && <ModelsStep {...stepProps} />}
        {current === "plan" && (
          <div className="stack">
            {!ready && <Notice tone="warn" title="Some answers need attention before a plan can be designed">Go back to step {(firstProblemStep(state) ?? 0) + 1}.</Notice>}
            {ready && design.phase === "designing" && <Loading label="Designing the plan: reading the target, choosing skills, classifying risk…" />}
            {ready && design.phase === "failed" && <ErrorNote error={design.error} onRetry={() => void begin()} />}
            {ready && !fresh && design.phase === "ready" && (
              <Notice tone="info" title="Your answers changed">
                The plan is being designed again for the new answers.
              </Notice>
            )}
          </div>
        )}
        {current === "execute" && (
          <div className="stack">
            <KeyValue items={summarise(state)} />
            {totals && design.phase === "ready" && design.out.plan && (
              <div className="grid" style={{ ["--min" as string]: "150px" }}>
                <Stat label="Tests to run" value={totals.selected} hint={deselected.size ? `${deselected.size} switched off` : undefined} />
                <Stat label="Will be blocked" value={totals.blocked} tone={totals.blocked > 0 ? "warn" : undefined} hint="Not failed" />
                <Stat label="Estimated time" value={formatDuration(design.out.plan.budget.est_wall_seconds)} />
                <Stat label="Estimated cost" value={design.out.plan.budget.est_cost_usd === null ? "n/a" : formatCost(design.out.plan.budget.est_cost_usd)} />
              </div>
            )}
            <Notice tone="info" title="What happens next">
              The run is queued and starts as soon as a worker is free. You can follow it live, stop it at a safe point at any time, and everything that ran is kept and reported even if you do.
            </Notice>
          </div>
        )}
      </Card>

      {current === "plan" && ready && fresh && design.phase === "ready" && (
        <div style={{ marginTop: 16 }}>
          <PlanView out={design.out} deselected={deselected} onToggle={toggle} />
        </div>
      )}

      <div className="row between" style={{ marginTop: 16 }}>
        <button type="button" className="btn" onClick={() => go(step - 1)} disabled={step === 0}>
          Back
        </button>
        {current === "execute" ? (
          <button type="button" className="btn primary" onClick={start} disabled={starting || design.phase !== "ready" || !fresh || (totals?.selected ?? 0) === 0}>
            <Icon name="play" size={16} /> {starting ? "Starting…" : `Start the run${totals ? ` (${totals.selected} tests)` : ""}`}
          </button>
        ) : (
          <button type="button" className="btn primary" onClick={next} disabled={current === "plan" && !(ready && fresh && design.phase === "ready")}>
            {current === "plan" ? "Next: execute" : "Next"}
          </button>
        )}
      </div>
    </>
  );
}
