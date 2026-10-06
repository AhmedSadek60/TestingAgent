import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ApiClient } from "../api/client";
import type { SseFrame } from "../api/sse";
import { event } from "../test/factories";
import { useRunFeed } from "./useRunFeed";

type Handlers = Parameters<ApiClient["follow"]>[1];

/** A client whose event stream the test drives by hand. */
function fakeClient() {
  const connections: { path: string; handlers: Handlers; finish: () => void; fail: (error: unknown) => void }[] = [];
  const follow = vi.fn((path: string, handlers: Handlers) => {
    return new Promise<void>((resolve, reject) => {
      connections.push({ path, handlers, finish: resolve, fail: reject });
      handlers.signal?.addEventListener("abort", () => resolve());
    });
  });
  return { client: { follow } as unknown as ApiClient, follow, connections };
}

const frame = (data: unknown, extra: Partial<SseFrame> = {}): SseFrame => ({
  event: "message",
  id: null,
  data: typeof data === "string" ? data : JSON.stringify(data),
  retry: null,
  comment: null,
  ...extra,
});

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useRunFeed", () => {
  it("follows the run's event stream and starts with nothing", () => {
    const { client, follow } = fakeClient();
    const { result } = renderHook(() => useRunFeed(client, "r1", "/runs/r1/events"));
    expect(follow).toHaveBeenCalledTimes(1);
    expect(follow.mock.calls[0]![0]).toBe("/runs/r1/events");
    expect(result.current.state.seen).toBe(0);
    expect(result.current.connected).toBe(false);
    expect(result.current.ended).toBeNull();
    expect(result.current.error).toBeNull();
  });

  it("applies events in batches rather than one at a time", () => {
    const { client, connections } = fakeClient();
    const { result } = renderHook(() => useRunFeed(client, "r1", "/runs/r1/events"));
    const { handlers } = connections[0]!;
    act(() => {
      handlers.onFrame(frame(event("RunStarted", { target: "Demo" })));
      handlers.onFrame(frame(event("TestStarted", { name: "Refuses", category: "security" }, { test_id: "t1" })));
    });
    expect(result.current.state.seen).toBe(0);
    act(() => void vi.advanceTimersByTime(150));
    expect(result.current.state.seen).toBe(2);
    expect(result.current.state.running).toEqual(["t1"]);
  });

  it("tells whether the stream is connected", () => {
    const { client, connections } = fakeClient();
    const { result } = renderHook(() => useRunFeed(client, "r1", "/runs/r1/events"));
    act(() => connections[0]!.handlers.onConnection?.(true));
    expect(result.current.connected).toBe(true);
    act(() => connections[0]!.handlers.onConnection?.(false));
    expect(result.current.connected).toBe(false);
  });

  it("skips a frame that is not an event instead of stopping", () => {
    const { client, connections } = fakeClient();
    const { result } = renderHook(() => useRunFeed(client, "r1", "/runs/r1/events"));
    const { handlers } = connections[0]!;
    act(() => {
      handlers.onFrame(frame("this is not json"));
      handlers.onFrame(frame(event("RunStarted")));
    });
    act(() => void vi.advanceTimersByTime(150));
    expect(result.current.state.seen).toBe(1);
    expect(result.current.error).toBeNull();
  });

  it("shows what was waiting and then says the run is over when the end event comes", () => {
    const { client, connections } = fakeClient();
    const { result } = renderHook(() => useRunFeed(client, "r1", "/runs/r1/events"));
    const { handlers } = connections[0]!;
    act(() => {
      handlers.onFrame(frame(event("RunCompleted")));
      handlers.onFrame(frame({ status: "completed", finished_at: "2026-10-06T10:00:11Z" }, { event: "end" }));
    });
    expect(result.current.ended).toEqual({ status: "completed", finished_at: "2026-10-06T10:00:11Z" });
    expect(result.current.state.terminal).toBe("RunCompleted");
  });

  it("takes an end event it cannot read as a normal ending", () => {
    const { client, connections } = fakeClient();
    const { result } = renderHook(() => useRunFeed(client, "r1", "/runs/r1/events"));
    act(() => connections[0]!.handlers.onFrame(frame("???", { event: "end" })));
    expect(result.current.ended).toEqual({ status: "completed", finished_at: null });
  });

  it("shows why the stream failed", async () => {
    const { client, connections } = fakeClient();
    const { result } = renderHook(() => useRunFeed(client, "r1", "/runs/r1/events"));
    await act(async () => connections[0]!.fail(new Error("No such run.")));
    expect(result.current.error).toBe("No such run.");
    expect(result.current.connected).toBe(false);
  });

  it("starts over, and stops following the old run, when it is given another one", () => {
    const { client, connections } = fakeClient();
    const { result, rerender } = renderHook(({ id }) => useRunFeed(client, id, `/runs/${id}/events`), { initialProps: { id: "r1" } });
    act(() => connections[0]!.handlers.onFrame(frame(event("RunStarted"))));
    act(() => void vi.advanceTimersByTime(150));
    expect(result.current.state.seen).toBe(1);

    rerender({ id: "r2" });
    expect(connections[0]!.handlers.signal?.aborted).toBe(true);
    expect(connections).toHaveLength(2);
    expect(connections[1]!.path).toBe("/runs/r2/events");
    expect(result.current.state.seen).toBe(0);
  });

  it("does not follow when it is not asked to", () => {
    const { client, follow } = fakeClient();
    renderHook(() => useRunFeed(client, "r1", "/runs/r1/events", false));
    expect(follow).not.toHaveBeenCalled();
  });

  it("lets go of the stream when the screen goes away", () => {
    const { client, connections } = fakeClient();
    const { unmount } = renderHook(() => useRunFeed(client, "r1", "/runs/r1/events"));
    unmount();
    expect(connections[0]!.handlers.signal?.aborted).toBe(true);
  });
});
