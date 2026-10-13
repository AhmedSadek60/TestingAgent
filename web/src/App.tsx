import { HashRouter, Link, Navigate, Route, Routes } from "react-router-dom";
import { Account } from "./pages/Account";
import { Accounts } from "./pages/Accounts";
import { ReporterLayout } from "./components/ReporterLayout";
import { Layout } from "./components/Layout";
import { Empty, ToastProvider } from "./components/ui";
import { Gate } from "./pages/SignIn";
import { Compare } from "./pages/Compare";
import { Credentials } from "./pages/Credentials";
import { Dashboard } from "./pages/Dashboard";
import { Findings } from "./pages/Findings";
import { LiveRun } from "./pages/LiveRun";
import { NewEvaluation } from "./pages/NewEvaluation";
import { PlanScreen, RunPlan } from "./pages/PlanScreen";
import { Providers } from "./pages/Providers";
import { Reports, RunReports } from "./pages/Reports";
import { Results } from "./pages/Results";
import { RunLayout } from "./pages/RunLayout";
import { Runs } from "./pages/Runs";
import { Scorecard } from "./pages/Scorecard";
import { Settings } from "./pages/Settings";
import { Skills } from "./pages/Skills";
import { TargetDetail } from "./pages/TargetDetail";
import { Targets } from "./pages/Targets";
import { Traces } from "./pages/Traces";
import { FeatureRequestForm } from "./pages/feedback/FeatureRequestForm";
import { FeedbackHome } from "./pages/feedback/FeedbackHome";
import { IssueReportForm } from "./pages/feedback/IssueReportForm";
import { SubmissionDetails } from "./pages/feedback/SubmissionDetails";
import { Submissions } from "./pages/feedback/Submissions";
import { SessionProvider, useSession } from "./session";

function NotFound() {
  return (
    <Empty title="There is nothing here">
      <Link to="/">Back to the dashboard</Link>
    </Empty>
  );
}

/** Feedback & Support: the same pages for everyone who may use them, whatever else they may use. */
function feedbackRoutes() {
  return (
    <>
      <Route path="feedback" element={<FeedbackHome />} />
      <Route path="feedback/feature" element={<FeatureRequestForm />} />
      <Route path="feedback/issue" element={<IssueReportForm />} />
      <Route path="feedback/submissions" element={<Submissions />} />
      <Route path="feedback/submissions/:ident" element={<SubmissionDetails />} />
    </>
  );
}

/** The routes of the interface. Hash routing keeps every screen a plain link the server does not have to know about. */
export function AppRoutes() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="new" element={<NewEvaluation />} />
        <Route path="targets" element={<Targets />} />
        <Route path="targets/:targetId" element={<TargetDetail />} />
        <Route path="plans/:runId" element={<PlanScreen />} />
        <Route path="runs" element={<Runs />} />
        <Route path="runs/:runId" element={<RunLayout />}>
          <Route index element={<LiveRun />} />
          <Route path="results" element={<Results />} />
          <Route path="findings" element={<Findings />} />
          <Route path="scorecard" element={<Scorecard />} />
          <Route path="traces" element={<Traces />} />
          <Route path="reports" element={<RunReports />} />
          <Route path="plan" element={<RunPlan />} />
          <Route path="*" element={<Navigate to=".." replace />} />
        </Route>
        <Route path="reports" element={<Reports />} />
        <Route path="compare" element={<Compare />} />
        <Route path="skills" element={<Skills />} />
        <Route path="providers" element={<Providers />} />
        <Route path="credentials" element={<Credentials />} />
        <Route path="settings" element={<Settings />} />
        <Route path="account" element={<Account />} />
        <Route path="accounts" element={<Accounts />} />
        {feedbackRoutes()}
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}

/** A person whose role reaches only Feedback & Support (or who has still to change a temporary password) gets a small interface
 * of their own: the rest of AgentLab would answer every call with a refusal. */
function ReporterRoutes() {
  const { me } = useSession();
  // a temporary password has to be changed first: until then the account page is the only one there is
  const locked = me?.must_change_password === true;
  return (
    <Routes>
      <Route element={<ReporterLayout />}>
        <Route index element={locked ? <Account /> : <FeedbackHome />} />
        <Route path="account" element={<Account />} />
        {!locked && feedbackRoutes()}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

export function Shell() {
  const { me } = useSession();
  return me && !me.operator ? <ReporterRoutes /> : <AppRoutes />;
}

export function App() {
  return (
    <HashRouter>
      <ToastProvider>
        <SessionProvider>
          <a className="sr-only" href="#content">
            Skip to the content
          </a>
          <Gate>
            <Shell />
          </Gate>
        </SessionProvider>
      </ToastProvider>
    </HashRouter>
  );
}
