import { useEffect, useReducer, useRef, useState } from "react";
import type { ApiClient } from "../api/client";
import { describeError } from "../api/errors";
import type { RunEvent } from "../api/types";
import { applyEvent, initialLive, type LiveState } from "../lib/live";

export interface RunFeed {
  state: LiveState;
  connected: boolean;
  /** The server said the run is over (the final `end` event arrived). */
  ended: { status: string; finished_at: string | null } | null;
  error: string | null;
}

type Action = { type: "events"; events: RunEvent[] } | { type: "reset" };

function reduce(state: LiveState, action: Action): LiveState {
  if (action.type === "reset") return initialLive();
  return action.events.reduce(applyEvent, state);
}

/**
 * Follow a run over server-sent events. The stream starts from the run's first event, so opening the screen late shows
 * everything that happened; events are applied in batches (a quick run can send thousands) so the screen stays responsive.
 */
export function useRunFeed(client: ApiClient, runId: string, streamPath: string, enabled = true): RunFeed {
  const [state, dispatch] = useReducer(reduce, undefined, initialLive);
  const [connected, setConnected] = useState(false);
  const [ended, setEnded] = useState<RunFeed["ended"]>(null);
  const [error, setError] = useState<string | null>(null);
  const queue = useRef<RunEvent[]>([]);

  useEffect(() => {
    if (!enabled) return;
    dispatch({ type: "reset" });
    setEnded(null);
    setError(null);
    queue.current = [];
    const controller = new AbortController();
    const flush = () => {
      if (queue.current.length > 0) {
        const events = queue.current;
        queue.current = [];
        dispatch({ type: "events", events });
      }
    };
    const timer = setInterval(flush, 150);
    client
      .follow(streamPath, {
        signal: controller.signal,
        onConnection: setConnected,
        onFrame: (frame) => {
          if (frame.event === "end") {
            flush();
            try {
              setEnded(JSON.parse(frame.data) as RunFeed["ended"]);
            } catch {
              setEnded({ status: "completed", finished_at: null });
            }
            return;
          }
          try {
            queue.current.push(JSON.parse(frame.data) as RunEvent);
          } catch {
            /* a frame that is not an event (it should not happen) is skipped rather than stopping the feed */
          }
        },
      })
      .catch((e: unknown) => {
        if (!controller.signal.aborted) setError(describeError(e));
      })
      .finally(() => {
        flush();
        setConnected(false);
      });
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [client, runId, streamPath, enabled]);

  return { state, connected, ended, error };
}
