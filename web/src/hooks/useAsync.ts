import { useCallback, useEffect, useRef, useState } from "react";
import { describeError } from "../api/errors";

export interface AsyncState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  /** Load again now (keeps showing the old data while it does). */
  reload: () => void;
  setData: (value: T | null | ((old: T | null) => T | null)) => void;
}

export interface AsyncOptions<T> {
  /** Reload this often while `until` has not said the data is final. */
  pollMs?: number;
  /** When it returns true the data is final and polling stops. */
  until?: (data: T) => boolean;
  /** Do not load while false (a form that is not open yet). */
  enabled?: boolean;
}

/**
 * Load something when the screen opens or its inputs change; cancel what is no longer wanted; optionally keep it fresh.
 * A refresh never blanks the screen: `loading` is true only until the first answer for the current inputs.
 */
export function useAsync<T>(load: (signal: AbortSignal) => Promise<T>, deps: readonly unknown[], options: AsyncOptions<T> = {}): AsyncState<T> {
  const { pollMs, until, enabled = true } = options;
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [tick, setTick] = useState(0);
  const loadRef = useRef(load);
  const untilRef = useRef(until);
  const dataRef = useRef(data);
  const inputsRef = useRef<readonly unknown[] | null>(null);
  loadRef.current = load;
  untilRef.current = until;
  dataRef.current = data;

  useEffect(() => {
    if (!enabled) {
      inputsRef.current = null;
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let first = true;
    // New inputs mean new data: what is on screen belongs to the old ones. A reload keeps it until the new answer arrives.
    const inputs = [...deps, enabled, pollMs];
    const previous = inputsRef.current;
    const same = previous !== null && previous.length === inputs.length && inputs.every((value, i) => Object.is(value, previous[i]));
    inputsRef.current = inputs;
    if (!same) setData(null);
    setLoading(!same || dataRef.current === null);
    setError(null);

    const run = async () => {
      try {
        const value = await loadRef.current(controller.signal);
        if (controller.signal.aborted) return;
        setData(value);
        setError(null);
        setLoading(false);
        const final = untilRef.current?.(value) ?? false;
        if (pollMs && !final) timer = setTimeout(run, pollMs);
      } catch (e) {
        if (controller.signal.aborted) return;
        setError(describeError(e));
        setLoading(false);
        if (pollMs && !first) timer = setTimeout(run, pollMs * 2); // a hiccup while following something: try again, slower
      }
      first = false;
    };
    void run();
    return () => {
      controller.abort();
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, enabled, pollMs, tick]);

  const reload = useCallback(() => setTick((n) => n + 1), []);
  return { data, error, loading, reload, setData };
}
