import type { DiagnosticCollection } from "../../api/types";
import { CATEGORY_LABELS, formatBytes, label } from "../../lib/feedback";
import { formatTime } from "../../lib/format";
import { Badge, Notice } from "../ui";
import { RedactionSummary } from "./shared";

const OUTCOME: Record<string, { tone: "good" | "warn" | "bad" | "neutral"; text: string }> = {
  collected: { tone: "good", text: "Collected" },
  unavailable: { tone: "neutral", text: "Not available" },
  failed: { tone: "bad", text: "Could not be collected" },
  excluded: { tone: "neutral", text: "Left out" },
};

/**
 * What was collected, item by item, as the person will share it: the source, the period, the size, what was masked, and
 * the masked text itself. A source that could not be collected says why and has nothing to tick.
 */
export function DiagnosticPreview({
  collection,
  include,
  onToggle,
  readOnly = false,
}: {
  collection: DiagnosticCollection;
  include: string[];
  onToggle?: (itemId: string, included: boolean) => void;
  readOnly?: boolean;
}) {
  return (
    <div className="stack">
      {collection.warnings.length > 0 && (
        <Notice tone="warn" title="Some of it could not be collected, or needs a look">
          <ul style={{ margin: 0, paddingLeft: 20 }}>
            {collection.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </Notice>
      )}
      <ul className="stack tight" style={{ listStyle: "none", margin: 0, padding: 0 }} aria-label="Diagnostic items">
        {collection.items.map((item) => {
          const outcome = OUTCOME[item.outcome] ?? OUTCOME.unavailable!;
          const canTick = !readOnly && item.outcome === "collected";
          const ticked = readOnly ? item.included : include.includes(item.id);
          const name = label(CATEGORY_LABELS, item.category);
          return (
            <li key={item.id} className="list-item">
              <div className="row between" style={{ gap: 12, flexWrap: "wrap" }}>
                <label className="check" style={{ opacity: canTick || readOnly ? 1 : 0.7 }}>
                  {!readOnly && (
                    <input
                      type="checkbox"
                      checked={ticked}
                      disabled={!canTick}
                      onChange={(event) => onToggle?.(item.id, event.target.checked)}
                      aria-label={`Include ${name}`}
                    />
                  )}
                  <span>
                    <strong>{name}</strong>
                  </span>
                </label>
                <Badge tone={outcome.tone}>{outcome.text}</Badge>
              </div>
              <div className="muted small">
                Source: {item.source}
                {item.time_from && item.time_to ? `. Period: ${formatTime(item.time_from)} to ${formatTime(item.time_to)}` : ""}
                {item.outcome === "collected" ? `. About ${formatBytes(item.size)}${item.truncated ? ", cut to the size limit" : ""}` : ""}
              </div>
              {item.outcome !== "collected" && item.detail && <p style={{ margin: "6px 0 0" }}>{item.detail}</p>}
              {item.outcome === "collected" && (
                <>
                  <div style={{ marginTop: 6 }}>
                    <RedactionSummary summary={item.redaction} />
                  </div>
                  {item.preview && (
                    <details style={{ marginTop: 6 }}>
                      <summary>Show what would be shared (masked)</summary>
                      <pre className="codebox" style={{ whiteSpace: "pre-wrap", maxHeight: 260, overflow: "auto" }}>
                        {item.preview}
                      </pre>
                    </details>
                  )}
                </>
              )}
            </li>
          );
        })}
      </ul>
      {collection.any_needs_review && (
        <Notice tone="warn" title="Look closely before you share this">
          Something in it still looks like a secret after masking. It stays on this server, but it should be looked at by a person before
          it goes anywhere else.
        </Notice>
      )}
    </div>
  );
}
