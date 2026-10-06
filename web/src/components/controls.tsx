import { useEffect, useState, type ReactNode } from "react";
import { statusLabel } from "../lib/labels";

/** A row of buttons that filter by one value; `""` means all. Shows how many items each value has. */
export function ChipFilter({
  options,
  value,
  onChange,
  label,
  allLabel = "All",
  total,
}: {
  options: { value: string; count: number }[];
  value: string;
  onChange: (value: string) => void;
  label: string;
  allLabel?: string;
  total?: number;
}) {
  const sum = total ?? options.reduce((n, o) => n + o.count, 0);
  return (
    <div className="row" role="group" aria-label={label}>
      <button type="button" className={`btn small${value === "" ? " primary" : ""}`} aria-pressed={value === ""} onClick={() => onChange("")}>
        {allLabel} ({sum})
      </button>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          className={`btn small${value === o.value ? " primary" : ""}`}
          aria-pressed={value === o.value}
          onClick={() => onChange(value === o.value ? "" : o.value)}
        >
          {statusLabel(o.value)} ({o.count})
        </button>
      ))}
    </div>
  );
}

/** A text box whose value is reported after the person pauses typing. */
export function SearchBox({ value, onChange, label, placeholder = "Search" }: { value: string; onChange: (value: string) => void; label: string; placeholder?: string }) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  useEffect(() => {
    if (text === value) return;
    const timer = setTimeout(() => onChange(text), 200);
    return () => clearTimeout(timer);
  }, [text, value, onChange]);
  return (
    <>
      <label className="sr-only" htmlFor={`search-${label}`}>
        {label}
      </label>
      <input id={`search-${label}`} className="input" type="search" placeholder={placeholder} value={text} onChange={(e) => setText(e.target.value)} />
    </>
  );
}

export function SelectFilter({ label, value, onChange, options, allLabel }: { label: string; value: string; onChange: (value: string) => void; options: { value: string; label?: string }[]; allLabel: string }) {
  const id = `filter-${label.replace(/\s+/g, "-").toLowerCase()}`;
  return (
    <>
      <label className="sr-only" htmlFor={id}>
        {label}
      </label>
      <select id={id} className="select" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{allLabel}</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label ?? o.value}
          </option>
        ))}
      </select>
    </>
  );
}

export function Checkbox({ checked, onChange, children, disabled, id }: { checked: boolean; onChange: (checked: boolean) => void; children: ReactNode; disabled?: boolean; id?: string }) {
  return (
    <label className="check">
      <input id={id} type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span>{children}</span>
    </label>
  );
}
