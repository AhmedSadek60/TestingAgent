import type { SubmissionType } from "../../api/types";

/** The two things a person can make, as two buttons that say what each is for. */
export function SubmissionTypeSelector({
  value,
  onChange,
  disabled = false,
}: {
  value: SubmissionType | null;
  onChange: (type: SubmissionType) => void;
  disabled?: boolean;
}) {
  return (
    <div className="choice-grid" role="group" aria-label="What would you like to send?">
      <button type="button" className="choice" aria-pressed={value === "feature"} disabled={disabled} onClick={() => onChange("feature")}>
        <strong>Request a feature</strong>
        <span className="muted small">Something new or better: a capability, an integration, a change to how you work.</span>
      </button>
      <button type="button" className="choice" aria-pressed={value === "issue"} disabled={disabled} onClick={() => onChange("issue")}>
        <strong>Report an issue</strong>
        <span className="muted small">Something that is wrong: an error, a wrong result, something slow or broken.</span>
      </button>
    </div>
  );
}
