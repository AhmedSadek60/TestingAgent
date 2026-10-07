import { useState } from "react";
import { describeError } from "../api/errors";
import type { Provider, ProviderCheck } from "../api/types";
import { DataTable } from "../components/DataTable";
import { Icon } from "../components/Icon";
import { Async, Badge, Card, Drawer, Empty, KeyValue, Notice, PageHeader, Tag, useToast } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { formatLatency, formatNumber } from "../lib/format";
import { useSession } from "../session";

function CheckResult({ check }: { check: ProviderCheck }) {
  return (
    <div className="stack tight" role="status">
      <div className="row">
        <Badge tone={check.ok ? "good" : "bad"}>{check.ok ? "working" : "not working"}</Badge>
        <span className="muted small">key: {check.key}</span>
      </div>
      <KeyValue
        items={[
          ["Models found", check.models === null ? null : `${formatNumber(check.models)}${check.discovery_ms !== null ? ` in ${formatLatency(check.discovery_ms)}` : ""}`],
          ["Model tried", check.model],
          ["Answer", check.completion ? `“${check.completion.slice(0, 160)}”` : null],
          ["Answered in", check.latency_ms === null ? null : `${formatLatency(check.latency_ms)}${check.tokens !== null ? `, ${formatNumber(check.tokens)} tokens` : ""}`],
          ["Problem", check.error ?? check.discovery_error ?? check.completion_error],
        ]}
      />
    </div>
  );
}

function Models({ provider, onClose }: { provider: Provider; onClose: () => void }) {
  const { api } = useSession();
  const models = useAsync((signal) => api.models(provider.name, signal), [api, provider.name]);
  return (
    <Drawer title={`Models of ${provider.name}`} onClose={onClose}>
      <Async state={models} loading="Asking the provider for its models…">
        {(list) => (
          <DataTable
            caption="Models"
            rows={list}
            rowKey={(m) => `${m.provider}:${m.model}`}
            pageSize={100}
            columns={[
              { key: "m", header: "Model", render: (m) => <span className="mono">{m.model ?? "unknown"}</span>, sort: (a, b) => (a.model ?? "").localeCompare(b.model ?? "") },
              { key: "c", header: "Context", numeric: true, render: (m) => (m.context ? formatNumber(m.context) : <span className="muted">n/a</span>) },
              { key: "k", header: "Capabilities", render: (m) => m.capabilities.map((c) => <Tag key={c}>{c}</Tag>) },
              { key: "e", header: "Note", render: (m) => <span className="small muted">{m.error}</span> },
            ]}
            empty={<Empty title="The provider listed no model" />}
          />
        )}
      </Async>
    </Drawer>
  );
}

/** The model providers the server can reach, and whether each one works. Keys live in the server's environment. */
export function Providers() {
  const { api } = useSession();
  const toast = useToast();
  const providers = useAsync((signal) => api.providers(signal), [api]);
  const [checks, setChecks] = useState<Record<string, ProviderCheck | "running">>({});
  const [models, setModels] = useState<Provider | null>(null);

  const check = async (name: string, complete: boolean) => {
    setChecks((c) => ({ ...c, [name]: "running" }));
    try {
      const out = await api.checkProvider(name, { complete });
      setChecks((c) => ({ ...c, [name]: out }));
    } catch (e) {
      setChecks((c) => {
        const { [name]: _gone, ...rest } = c;
        void _gone;
        return rest;
      });
      toast.push("error", describeError(e));
    }
  };

  return (
    <>
      <PageHeader title="Providers" subtitle="The model providers AgentLab can use as the target, the judge or a test designer. They are set in the server's configuration; keys are read from its environment and never entered here." />
      <Async state={providers}>
        {(list) =>
          list.length === 0 ? (
            <Card>
              <Empty title="No provider is configured">Add one under <span className="mono">providers:</span> in the server's configuration file.</Empty>
            </Card>
          ) : (
            <div className="stack" style={{ gap: 16 }}>
              {!list.some((p) => p.judge) && (
                <Notice tone="warn" title="No judge provider">
                  Criteria that need a judge model cannot be scored, and those tests are blocked. Mark a provider as the judge in the configuration.
                </Notice>
              )}
              {list.map((p) => {
                const result = checks[p.name];
                return (
                  <Card
                    key={p.name}
                    title={
                      <span className="row">
                        {p.name} <Tag>{p.type}</Tag> {p.judge && <Badge tone="info">judge</Badge>}
                      </span>
                    }
                    actions={
                      <>
                        <button type="button" className="btn small" onClick={() => setModels(p)}>
                          <Icon name="list" size={14} /> Models
                        </button>
                        <button type="button" className="btn small" disabled={result === "running"} onClick={() => void check(p.name, false)}>
                          <Icon name="refresh" size={14} /> Check connection
                        </button>
                        <button type="button" className="btn small" disabled={result === "running"} onClick={() => void check(p.name, true)} title="Sends one tiny request; it may cost a fraction of a cent">
                          <Icon name="play" size={14} /> Try a request
                        </button>
                      </>
                    }
                  >
                    <div className="stack">
                      {p.configured_error && (
                        <Notice tone="error" title="This provider is not usable as configured">
                          {p.configured_error}
                        </Notice>
                      )}
                      <KeyValue
                        items={[
                          ["Model", p.model],
                          ["Address", p.base_url ?? "the provider's default"],
                          ["Key", p.key],
                          ["Capabilities", p.capabilities.length ? p.capabilities.map((c) => <Tag key={c}>{c}</Tag>) : null],
                        ]}
                      />
                      {result === "running" && <span className="muted">Checking…</span>}
                      {result && result !== "running" && <CheckResult check={result} />}
                    </div>
                  </Card>
                );
              })}
            </div>
          )
        }
      </Async>
      {models && <Models provider={models} onClose={() => setModels(null)} />}
    </>
  );
}
