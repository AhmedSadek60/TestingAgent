/** Browser storage that may be missing or refuse to work (private windows, blocked site data): every call is guarded and the
 * interface works without it. The API token only ever goes to `sessionStorage` (this tab, gone when it closes). */
type Kind = "local" | "session";

function area(kind: Kind): Storage | null {
  try {
    return kind === "local" ? window.localStorage : window.sessionStorage;
  } catch {
    return null;
  }
}

export function readStored(kind: Kind, key: string): string | null {
  try {
    return area(kind)?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

export function writeStored(kind: Kind, key: string, value: string | null): void {
  try {
    const store = area(kind);
    if (!store) return;
    if (value === null) store.removeItem(key);
    else store.setItem(key, value);
  } catch {
    /* storage is full or blocked: the setting simply is not remembered */
  }
}

export const TOKEN_KEY = "agentlab.token";
export const PROJECT_KEY = "agentlab.project";
export const THEME_KEY = "agentlab.theme";
