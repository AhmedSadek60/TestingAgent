import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { NavLink } from "react-router-dom";
import type { AsyncState } from "../hooks/useAsync";
import { formatScore } from "../lib/format";
import { gradeTone, scoreTone, severityTone, splitGrade, statusLabel, statusTone, type Tone } from "../lib/labels";
import { Icon } from "./Icon";

// ======================================================================================================== badges
export function Badge({ tone = "neutral", children, pulse = false, title }: { tone?: Tone; children: ReactNode; pulse?: boolean; title?: string }) {
  return (
    <span className={`badge ${tone}${pulse ? " pulse" : ""}`} title={title}>
      {pulse && <span className="dot" aria-hidden="true" />}
      {children}
    </span>
  );
}

export function StatusBadge({ status }: { status: string | null | undefined }) {
  if (!status) return <Badge>unknown</Badge>;
  const live = status === "running" || status === "cancelling";
  return (
    <Badge tone={statusTone(status)} pulse={live}>
      {statusLabel(status)}
    </Badge>
  );
}

export function SeverityBadge({ severity }: { severity: string | null | undefined }) {
  if (!severity) return <Badge>none</Badge>;
  return <Badge tone={severityTone(severity)}>{severity}</Badge>;
}

export function GradeBadge({ grade }: { grade: string | null | undefined }) {
  if (!grade) return <Badge>not graded</Badge>;
  const { letter, notes } = splitGrade(grade);
  return (
    <Badge tone={gradeTone(grade)} title={notes.length ? `Qualified: ${notes.join("; ")}` : undefined}>
      {letter}
    </Badge>
  );
}

export function Tag({ children }: { children: ReactNode }) {
  return <span className="tag">{children}</span>;
}

// ====================================================================================================== structure
export function Card({
  title,
  actions,
  children,
  padded = true,
  className = "",
  id,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  padded?: boolean;
  className?: string;
  id?: string;
}) {
  return (
    <section className={`card ${className}`} id={id}>
      {(title || actions) && (
        <div className="card-header">
          {title && <h2>{title}</h2>}
          {actions}
        </div>
      )}
      {padded ? <div className="card-body">{children}</div> : children}
    </section>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }) {
  useEffect(() => {
    document.title = typeof title === "string" ? `${title} · AgentLab` : "AgentLab";
  }, [title]);
  return (
    <header className="page-header">
      <div className="titles">
        <h1>{title}</h1>
        {subtitle && <p>{subtitle}</p>}
      </div>
      {actions && <div className="row">{actions}</div>}
    </header>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children && <div>{children}</div>}
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return <span className="spinner" role="status" aria-label={label} />;
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="row" style={{ padding: 24, justifyContent: "center" }} role="status">
      <span className="spinner" aria-hidden="true" />
      <span className="muted">{label}</span>
    </div>
  );
}

export function Notice({ tone = "info", title, children }: { tone?: "info" | "warn" | "error" | "good"; title?: string; children?: ReactNode }) {
  const icon = tone === "good" ? "check" : tone === "info" ? "info" : "alert";
  return (
    <div className={`alert ${tone}`} role={tone === "error" ? "alert" : "status"}>
      <Icon name={icon} />
      <div>
        {title && <strong>{title}</strong>}
        {children && <div>{children}</div>}
      </div>
    </div>
  );
}

export function ErrorNote({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return (
    <div className="alert error" role="alert">
      <Icon name="alert" />
      <div className="row between" style={{ width: "100%" }}>
        <span>{error}</span>
        {onRetry && (
          <button type="button" className="btn small" onClick={onRetry}>
            <Icon name="refresh" size={14} /> Try again
          </button>
        )}
      </div>
    </div>
  );
}

/** Show what an `useAsync` is doing: loading, failed (with a retry) or its data. */
export function Async<T>({ state, children, loading }: { state: AsyncState<T>; children: (data: T) => ReactNode; loading?: string }) {
  if (state.data !== null) return <>{children(state.data)}</>;
  if (state.error) return <ErrorNote error={state.error} onRetry={state.reload} />;
  return <Loading label={loading} />;
}

export function Stat({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: Tone }) {
  return (
    <div className="card stat">
      <div className="label">{label}</div>
      <div className="value" style={tone && tone !== "neutral" ? { color: `var(--${tone})` } : undefined}>
        {value}
      </div>
      {hint && <div className="muted small">{hint}</div>}
    </div>
  );
}

export function KeyValue({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="kv">
      {items.map(([label, value]) => (
        <div key={label} style={{ display: "contents" }}>
          <dt>{label}</dt>
          <dd>{value ?? <span className="muted">n/a</span>}</dd>
        </div>
      ))}
    </dl>
  );
}

// ======================================================================================================== charts
export function ProgressBar({ value, max = 100, tone, label }: { value: number; max?: number; tone?: "good" | "bad" | "warn"; label: string }) {
  const percent = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return (
    <div
      className={`progress ${tone ?? ""}`}
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={Math.round(value)}
    >
      <span style={{ width: `${percent}%` }} />
    </div>
  );
}

export function ScoreRing({ score, grade, size = 150, caption }: { score: number | null; grade?: string | null; size?: number; caption?: string }) {
  const radius = (size - 16) / 2;
  const circumference = 2 * Math.PI * radius;
  const fraction = score === null ? 0 : Math.max(0, Math.min(1, score / 100));
  const tone = scoreTone(score);
  const { letter, notes } = splitGrade(grade);
  const spoken = score === null ? "Not scored" : `Score ${formatScore(score)} of 100${letter ? `, grade ${letter}` : ""}${notes.length ? `, qualified: ${notes.join("; ")}` : ""}`;
  return (
    <div className="score-ring" style={{ width: size, height: size }} title={notes.length ? `Qualified: ${notes.join("; ")}` : undefined}>
      <svg width={size} height={size} role="img" aria-label={spoken}>
        <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="var(--surface-2)" strokeWidth="10" />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={tone === "neutral" ? "var(--border-strong)" : `var(--${tone})`}
          strokeWidth="10"
          strokeLinecap="round"
          strokeDasharray={`${circumference * fraction} ${circumference}`}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
      </svg>
      <div className="center">
        <strong style={{ fontSize: Math.round(size * 0.28) }}>{score === null ? "n/a" : formatScore(score)}</strong>
        <span className="muted small">{letter ? (size < 110 ? letter : `Grade ${letter}`) : (caption ?? (size < 110 ? "" : "of 100"))}</span>
      </div>
    </div>
  );
}

export function BarRow({ label, value, note, confidence }: { label: string; value: number | null; note?: string | null; confidence?: number }) {
  const scored = scoreTone(value);
  const tone = scored === "good" || scored === "warn" || scored === "bad" ? scored : undefined;
  return (
    <div className="bar-row">
      <span title={note ?? undefined}>{label}</span>
      {value === null ? <span className="muted small">{note ?? "not scored"}</span> : <ProgressBar value={value} tone={tone} label={`${label}: ${formatScore(value)}`} />}
      <span className="num" style={{ textAlign: "right" }} title={confidence === undefined ? undefined : `Confidence ${(confidence * 100).toFixed(0)}%`}>
        {value === null ? "n/a" : formatScore(value)}
      </span>
    </div>
  );
}

// ====================================================================================================== buttons
export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className="btn small ghost"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setDone(true);
          setTimeout(() => setDone(false), 1500);
        } catch {
          setDone(false);
        }
      }}
    >
      <Icon name={done ? "check" : "copy"} size={14} /> {done ? "Copied" : label}
    </button>
  );
}

/** Save text or a blob as a file the person chooses to keep. */
export function saveBlob(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

// ========================================================================================================== tabs
export function NavTabs({ items, label }: { items: { to: string; label: ReactNode; end?: boolean }[]; label: string }) {
  return (
    <nav className="tabs" aria-label={label}>
      {items.map((item) => (
        <NavLink key={item.to} to={item.to} end={item.end} className={({ isActive }) => (isActive ? "active" : "")}>
          {item.label}
        </NavLink>
      ))}
    </nav>
  );
}

export function StateTabs<T extends string>({ items, value, onChange, label }: { items: { id: T; label: ReactNode }[]; value: T; onChange: (id: T) => void; label: string }) {
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {items.map((item) => (
        <button key={item.id} type="button" role="tab" aria-selected={item.id === value} onClick={() => onChange(item.id)}>
          {item.label}
        </button>
      ))}
    </div>
  );
}

// ========================================================================================================= dialogs
function useDialog(onClose: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const node = ref.current;
    const first = node?.querySelector<HTMLElement>("button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])");
    (first ?? node)?.focus();
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key === "Tab" && node) {
        const items = Array.from(node.querySelectorAll<HTMLElement>("button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled)"));
        if (items.length === 0) return;
        const firstItem = items[0];
        const lastItem = items[items.length - 1];
        if (event.shiftKey && document.activeElement === firstItem) {
          event.preventDefault();
          lastItem.focus();
        } else if (!event.shiftKey && document.activeElement === lastItem) {
          event.preventDefault();
          firstItem.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
      previous?.focus?.();
    };
  }, [onClose]);
  return ref;
}

export function Drawer({ title, onClose, children }: { title: ReactNode; onClose: () => void; children: ReactNode }) {
  const ref = useDialog(onClose);
  const titleId = useId();
  return createPortal(
    <div className="overlay right" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="drawer" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={ref} tabIndex={-1}>
        <header>
          <h2 id={titleId} className="grow">
            {title}
          </h2>
          <button type="button" className="btn ghost" onClick={onClose} aria-label="Close">
            <Icon name="x" />
          </button>
        </header>
        <div className="body">{children}</div>
      </div>
    </div>,
    document.body,
  );
}

export function Modal({ title, onClose, children, footer }: { title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode }) {
  const ref = useDialog(onClose);
  const titleId = useId();
  return createPortal(
    <div className="overlay center" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={ref} tabIndex={-1}>
        <header>
          <h2 id={titleId} className="grow">
            {title}
          </h2>
          <button type="button" className="btn ghost" onClick={onClose} aria-label="Close">
            <Icon name="x" />
          </button>
        </header>
        <div className="body">{children}</div>
        {footer && <footer>{footer}</footer>}
      </div>
    </div>,
    document.body,
  );
}

export function ConfirmDialog({
  title,
  confirmLabel,
  danger = false,
  busy = false,
  onConfirm,
  onClose,
  children,
}: {
  title: string;
  confirmLabel: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <Modal
      title={title}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Keep it
          </button>
          <button type="button" className={`btn ${danger ? "danger solid" : "primary"}`} onClick={onConfirm} disabled={busy}>
            {confirmLabel}
          </button>
        </>
      }
    >
      {children}
    </Modal>
  );
}

// ========================================================================================================= toasts
interface ToastItem {
  id: number;
  tone: "info" | "good" | "warn" | "error";
  text: string;
}
const ToastContext = createContext<{ push: (tone: ToastItem["tone"], text: string) => void } | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const next = useRef(1);
  const push = useCallback((tone: ToastItem["tone"], text: string) => {
    const id = next.current++;
    setItems((current) => [...current.slice(-3), { id, tone, text }]);
    setTimeout(() => setItems((current) => current.filter((t) => t.id !== id)), tone === "error" ? 9000 : 4500);
  }, []);
  const value = useMemo(() => ({ push }), [push]);
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toasts" aria-live="polite">
        {items.map((item) => (
          <div key={item.id} className={`alert ${item.tone} toast`}>
            <Icon name={item.tone === "good" ? "check" : item.tone === "info" ? "info" : "alert"} />
            <span>{item.text}</span>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const value = useContext(ToastContext);
  if (!value) throw new Error("useToast must be used inside <ToastProvider>");
  return value;
}

// ========================================================================================================= fields
export function Field({
  label,
  help,
  error,
  children,
  htmlFor,
  className = "",
}: {
  label: string;
  help?: ReactNode;
  error?: string | null;
  children: ReactNode;
  htmlFor?: string;
  className?: string;
}) {
  return (
    <div className={`field ${className}`}>
      <label htmlFor={htmlFor}>{label}</label>
      {children}
      {help && <span className="help">{help}</span>}
      {error && (
        <span className="error-text" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}
