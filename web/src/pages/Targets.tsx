import { Link, useNavigate } from "react-router-dom";
import { DataTable } from "../components/DataTable";
import { Icon } from "../components/Icon";
import { Async, Card, Empty, PageHeader, Tag } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { formatTime, relativeTime } from "../lib/format";
import { useSession } from "../session";

/** Every target registered in the project, and the way into discovery and evaluation. */
export function Targets() {
  const { api, project } = useSession();
  const navigate = useNavigate();
  const targets = useAsync((signal) => api.targets(project, signal), [api, project]);
  return (
    <>
      <PageHeader
        title="Targets and discovery"
        subtitle="The agents registered in this project. Discovery learns what an agent is and can do before anything is tested."
        actions={
          <Link to="/new?mode=discovery" className="btn primary">
            <Icon name="plus" /> Add a target
          </Link>
        }
      />
      <Card padded={false}>
        <Async state={targets}>
          {(list) => (
            <DataTable
              caption="Targets"
              rows={list}
              rowKey={(t) => t.id}
              onRowClick={(t) => navigate(`/targets/${t.id}`)}
              initialSort={{ key: "updated", descending: true }}
              empty={
                <Empty title="No target yet">
                  <p>
                    A target is registered when you design a plan for it. <Link to="/new">Start a new evaluation</Link>, or try the built-in demonstration agent.
                  </p>
                </Empty>
              }
              columns={[
                { key: "name", header: "Target", render: (t) => <><strong>{t.name}</strong>{t.target_version && <span className="muted"> {t.target_version}</span>}<div className="muted small mono">{t.id.slice(0, 8)}</div></>, sort: (a, b) => a.name.localeCompare(b.name) },
                { key: "kind", header: "Reached through", render: (t) => <Tag>{t.kind}</Tag>, sort: (a, b) => a.kind.localeCompare(b.kind) },
                { key: "updated", header: "Updated", render: (t) => <span title={formatTime(t.updated_at)}>{relativeTime(t.updated_at)}</span>, sort: (a, b) => a.updated_at.localeCompare(b.updated_at) },
                {
                  key: "actions",
                  header: <span className="sr-only">Actions</span>,
                  render: (t) => (
                    <Link className="btn small" to={`/new?target=${encodeURIComponent(t.id)}`} onClick={(e) => e.stopPropagation()}>
                      <Icon name="play" size={14} /> Evaluate
                    </Link>
                  ),
                },
              ]}
            />
          )}
        </Async>
      </Card>
    </>
  );
}
