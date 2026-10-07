import { useState } from "react";

const MAX_STRING = 1200;
const MAX_ITEMS = 100;
const REDACTION = /(\[REDACTED[^\]]*\])/g;
const IS_REDACTION = /^\[REDACTED[^\]]*\]$/;

/** A string with the server's `[REDACTED:...]` markers made visible. Always rendered as text, never as markup. */
export function RedactedText({ text }: { text: string }) {
  const parts = text.split(REDACTION);
  return (
    <>
      {parts.map((part, index) =>
        IS_REDACTION.test(part) ? (
          <span key={index} className="redacted" title="The server hid a secret here before storing it">
            {part}
          </span>
        ) : (
          <span key={index}>{part}</span>
        ),
      )}
    </>
  );
}

function Scalar({ value }: { value: string | number | boolean | null }) {
  const [full, setFull] = useState(false);
  if (value === null) return <span className="muted">null</span>;
  if (typeof value === "number" || typeof value === "boolean") return <span className="n">{String(value)}</span>;
  const long = value.length > MAX_STRING && !full;
  return (
    <span className="s">
      &quot;
      <RedactedText text={long ? value.slice(0, MAX_STRING) : value} />
      {long && (
        <>
          …{" "}
          <button type="button" className="btn small ghost" onClick={() => setFull(true)}>
            show all {value.length.toLocaleString()} characters
          </button>
        </>
      )}
      &quot;
    </span>
  );
}

function Node({ name, value, depth }: { name?: string; value: unknown; depth: number }) {
  const label = name === undefined ? null : <span className="k">{name}: </span>;
  if (value === null || typeof value !== "object") {
    return (
      <div style={{ marginLeft: 14 }}>
        {label}
        <Scalar value={value as string | number | boolean | null} />
      </div>
    );
  }
  const isArray = Array.isArray(value);
  const entries: [string, unknown][] = isArray ? (value as unknown[]).map((item, index) => [String(index), item]) : Object.entries(value as Record<string, unknown>);
  if (entries.length === 0) {
    return (
      <div style={{ marginLeft: 14 }}>
        {label}
        <span className="muted">{isArray ? "[]" : "{}"}</span>
      </div>
    );
  }
  const shown = entries.slice(0, MAX_ITEMS);
  return (
    <details open={depth < 2} style={{ marginLeft: 14 }}>
      <summary>
        {label}
        <span className="muted">
          {isArray ? `[${entries.length}]` : `{${entries.length}}`}
        </span>
      </summary>
      {shown.map(([key, item]) => (
        <Node key={key} name={isArray ? undefined : key} value={item} depth={depth + 1} />
      ))}
      {entries.length > shown.length && <div className="muted small" style={{ marginLeft: 14 }}>… and {entries.length - shown.length} more</div>}
    </details>
  );
}

export function JsonView({ value }: { value: unknown }) {
  return (
    <div className="json codebox mono" role="group" aria-label="Structured data">
      <Node value={value} depth={0} />
    </div>
  );
}

/** Text that came out of the agent under test: shown as plain text, labelled so nobody mistakes it for the product's own. */
export function Untrusted({ text, label = "From the agent under test" }: { text: string; label?: string }) {
  return (
    <div className="untrusted">
      <div className="label">{label} · untrusted, shown as text</div>
      <pre>
        <RedactedText text={text} />
      </pre>
    </div>
  );
}
