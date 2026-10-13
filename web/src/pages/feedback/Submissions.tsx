import { Link, useSearchParams } from "react-router-dom";
import { Badge, Empty, ErrorNote, Loading, PageHeader } from "../../components/ui";
import { SearchBox, SelectFilter } from "../../components/controls";
import { StateTabs } from "../../components/ui";
import { PrivateBadge, TypeBadge, WorkflowBadge } from "../../components/feedback/shared";
import { useAsync } from "../../hooks/useAsync";
import type { SubmissionQuery, SubmissionSummary } from "../../api/types";
import { FEATURE_PRIORITY_LABELS, PRIORITY_LABELS, label } from "../../lib/feedback";
import { formatTime, plural, relativeTime } from "../../lib/format";
import { useSession } from "../../session";

const PAGE = 25;
const KEYS = ["type", "category", "q", "priority", "severity", "assignee", "label", "visibility", "version", "run", "sort", "order", "offset"] as const;

function queryFrom(params: URLSearchParams): SubmissionQuery {
  const query: SubmissionQuery = { limit: PAGE, offset: Number(params.get("offset") ?? 0) || 0 };
  const type = params.get("type");
  if (type === "feature" || type === "issue") query.type = type;
  const statuses = params.getAll("status");
  if (statuses.length) query.status = statuses;
  for (const key of ["category", "q", "priority", "severity", "assignee", "label", "visibility", "version", "run"] as const) {
    const value = params.get(key);
    if (value) query[key] = value;
  }
  if (params.get("unread") === "true") query.unread = true;
  if (params.get("mine") === "true") query.mine = true;
  const sort = params.get("sort");
  if (sort === "activity" || sort === "created" || sort === "priority") query.sort = sort;
  if (params.get("order") === "asc") query.order = "asc";
  return query;
}

/** The list of submissions: a reporter's own, or (for a maintainer) the queue, with the filters the work needs. The page the
 * server sends is the page shown: sorting and filtering are the server's, so it holds for any number of submissions. */
export function Submissions() {
  const { api } = useSession();
  const [params, setParams] = useSearchParams();
  const options = useAsync((signal) => api.feedbackOptions(signal), [api]);
  const staff = options.data?.can_triage === true;
  const query = queryFrom(params);
  const list = useAsync((signal) => api.submissions(query, signal), [api, params.toString()]);

  const change = (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) {
      if (value === null || value === "") next.delete(key);
      else next.set(key, value);
    }
    if (!("offset" in changes)) next.delete("offset");
    setParams(next, { replace: true });
  };
  const setStatus = (status: string) => {
    const next = new URLSearchParams(params);
    next.delete("status");
    if (status) next.set("status", status);
    next.delete("offset");
    setParams(next, { replace: true });
  };

  const statuses = Array.from(new Set([...(options.data?.feature_statuses ?? []), ...(options.data?.issue_statuses ?? [])]));
  const categories = Array.from(new Set([...(options.data?.feature_categories ?? []), ...(options.data?.issue_categories ?? [])]));
  const page = list.data;
  const offset = query.offset ?? 0;
  const type = params.get("type") ?? "";
  return (
    <>
      <PageHeader
        title={staff ? "Submissions" : "My submissions"}
        subtitle={staff ? "Feature requests and issue reports, newest activity first." : "What you have sent, and what has happened to it."}
        actions={
          <>
            <Link className="btn" to="/feedback/feature">
              Request a feature
            </Link>
            <Link className="btn primary" to="/feedback/issue">
              Report an issue
            </Link>
          </>
        }
      />
      <div className="stack">
        <StateTabs
          label="Type"
          value={(type as "" | "feature" | "issue") || ""}
          onChange={(value) => change({ type: value || null })}
          items={[
            { id: "", label: "Everything" },
            { id: "feature", label: "Feature requests" },
            { id: "issue", label: "Issue reports" },
          ]}
        />
        <div className="table-tools">
          <SearchBox label="Search submissions" value={params.get("q") ?? ""} onChange={(q) => change({ q: q || null })} placeholder="Title, description, number, run or test id" />
          <SelectFilter
            label="Status"
            value={params.get("status") ?? ""}
            onChange={setStatus}
            options={statuses.map((s) => ({ value: s }))}
            allLabel="Any status"
          />
          <SelectFilter
            label="Category"
            value={params.get("category") ?? ""}
            onChange={(category) => change({ category })}
            options={categories.map((c) => ({ value: c }))}
            allLabel="Any category"
          />
          {staff ? (
            <>
              <SelectFilter
                label="Assignee"
                value={params.get("assignee") ?? ""}
                onChange={(assignee) => change({ assignee })}
                options={[
                  { value: "me", label: "Assigned to me" },
                  { value: "none", label: "Not assigned" },
                ]}
                allLabel="Any assignee"
              />
              <SelectFilter
                label="Priority"
                value={params.get("priority") ?? ""}
                onChange={(priority) => change({ priority })}
                options={(options.data?.priorities ?? []).map((p) => ({ value: p, label: label(PRIORITY_LABELS, p) }))}
                allLabel="Any priority"
              />
              <SelectFilter
                label="Severity"
                value={params.get("severity") ?? ""}
                onChange={(severity) => change({ severity })}
                options={(options.data?.severities ?? []).map((s) => ({ value: s }))}
                allLabel="Any severity"
              />
              <SelectFilter
                label="Visibility"
                value={params.get("visibility") ?? ""}
                onChange={(visibility) => change({ visibility })}
                options={[{ value: "security", label: "Private (security) only" }]}
                allLabel="Any visibility"
              />
            </>
          ) : (
            <label className="check">
              <input type="checkbox" checked={params.get("unread") === "true"} onChange={(e) => change({ unread: e.target.checked ? "true" : null })} />
              <span>With an answer I have not read</span>
            </label>
          )}
          <SelectFilter
            label="Sort"
            value={params.get("sort") ?? ""}
            onChange={(sort) => change({ sort })}
            options={[
              { value: "activity", label: "Last activity" },
              { value: "created", label: "Date made" },
              ...(staff ? [{ value: "priority", label: "Priority" }] : []),
            ]}
            allLabel="Sort: last activity"
          />
        </div>
        {list.loading && !page && <Loading />}
        {list.error && !page && <ErrorNote error={list.error} onRetry={list.reload} />}
        {page && page.items.length === 0 && (
          <Empty title={KEYS.some((k) => params.get(k)) || params.get("status") ? "Nothing matches" : "Nothing here yet"}>
            {params.toString() ? "Try other words or clear a filter." : "When you send a feature request or an issue report it will be listed here."}
          </Empty>
        )}
        {page && page.items.length > 0 && (
          <>
            <div className="table-wrap">
              <table className="table">
                <caption className="sr-only">Submissions</caption>
                <thead>
                  <tr>
                    <th scope="col">ID</th>
                    <th scope="col">Title</th>
                    <th scope="col">Type</th>
                    <th scope="col">Category</th>
                    <th scope="col">Status</th>
                    {staff ? (
                      <>
                        <th scope="col">Priority</th>
                        <th scope="col">Severity</th>
                        <th scope="col">Assigned to</th>
                        <th scope="col">Reporter</th>
                      </>
                    ) : (
                      <th scope="col">Requested priority</th>
                    )}
                    <th scope="col">Made</th>
                    <th scope="col">Updated</th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((s) => (
                    <Row key={s.id} s={s} staff={staff} />
                  ))}
                </tbody>
              </table>
            </div>
            <div className="row between">
              <span className="muted">
                {offset + 1} to {offset + page.items.length} of {plural(page.total, "submission")}
              </span>
              <span className="row">
                <button type="button" className="btn" disabled={offset === 0} onClick={() => change({ offset: String(Math.max(0, offset - PAGE)) })}>
                  Previous
                </button>
                <button
                  type="button"
                  className="btn"
                  disabled={offset + page.items.length >= page.total}
                  onClick={() => change({ offset: String(offset + PAGE) })}
                >
                  Next
                </button>
              </span>
            </div>
          </>
        )}
      </div>
    </>
  );
}

function Row({ s, staff }: { s: SubmissionSummary; staff: boolean }) {
  return (
    <tr>
      <td className="mono nowrap">
        <Link to={`/feedback/submissions/${s.number}`}>{s.number}</Link>
      </td>
      <td>
        <Link to={`/feedback/submissions/${s.number}`}>{s.title}</Link>{" "}
        {s.visibility === "security" && <PrivateBadge />} {s.unread && <Badge tone="info">Answer waiting</Badge>}
      </td>
      <td>
        <TypeBadge type={s.type} />
      </td>
      <td>{s.category}</td>
      <td>
        <WorkflowBadge status={s.status} />
      </td>
      {staff ? (
        <>
          <td>{s.assigned_priority ? label(PRIORITY_LABELS, s.assigned_priority) : <span className="muted">not set</span>}</td>
          <td>{s.severity ?? <span className="muted">not set</span>}</td>
          <td>{s.assignee?.name ?? <span className="muted">nobody</span>}</td>
          <td>{s.reporter}</td>
        </>
      ) : (
        <td>{s.type === "feature" && s.requested_priority ? label(FEATURE_PRIORITY_LABELS, s.requested_priority) : <span className="muted">n/a</span>}</td>
      )}
      <td className="nowrap" title={formatTime(s.created_at)}>
        {relativeTime(s.created_at)}
      </td>
      <td className="nowrap" title={formatTime(s.updated_at)}>
        {relativeTime(s.last_activity_at)}
      </td>
    </tr>
  );
}
