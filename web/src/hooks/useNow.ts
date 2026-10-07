import { useEffect, useState } from "react";

/** The current time, refreshed every `everyMs` while `active` (so a running clock does not tick for a finished run). */
export function useNow(active: boolean, everyMs = 1000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    if (!active) return;
    setNow(new Date());
    const timer = setInterval(() => setNow(new Date()), everyMs);
    return () => clearInterval(timer);
  }, [active, everyMs]);
  return now;
}
