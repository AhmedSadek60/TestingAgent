import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/errors";
import { useAsync } from "./useAsync";

/** A load whose answers the test hands out one at a time. */
function controlled<T>() {
  const pending: { resolve: (value: T) => void; reject: (error: unknown) => void; signal: AbortSignal }[] = [];
  const load = vi.fn(
    (signal: AbortSignal) =>
      new Promise<T>((resolve, reject) => {
        pending.push({ resolve, reject, signal });
      }),
  );
  return { load, pending };
}

afterEach(() => {
  vi.useRealTimers();
});

describe("useAsync", () => {
  it("is loading until the first answer, then has the data", async () => {
    const { result } = renderHook(() => useAsync(async () => "hello", []));
    expect(result.current.loading).toBe(true);
    expect(result.current.data).toBeNull();
    await waitFor(() => expect(result.current.data).toBe("hello"));
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("says what went wrong in words", async () => {
    const { result } = renderHook(() =>
      useAsync(async () => {
        throw new ApiError(404, "not_found", "No such run.");
      }, []),
    );
    await waitFor(() => expect(result.current.error).toBe("No such run."));
    expect(result.current.loading).toBe(false);
    expect(result.current.data).toBeNull();
  });

  it("starts again with nothing on screen when its inputs change, and drops a late answer for the old ones", async () => {
    const { load, pending } = controlled<string>();
    const { result, rerender } = renderHook(({ id }) => useAsync((signal) => load(signal), [id]), { initialProps: { id: "a" } });
    await waitFor(() => expect(pending).toHaveLength(1));
    await act(async () => pending[0]!.resolve("answer for a"));
    await waitFor(() => expect(result.current.data).toBe("answer for a"));

    rerender({ id: "b" });
    await waitFor(() => expect(pending).toHaveLength(2));
    expect(result.current.data).toBeNull();
    expect(result.current.loading).toBe(true);

    rerender({ id: "c" });
    await waitFor(() => expect(pending).toHaveLength(3));
    expect(pending[1]!.signal.aborted).toBe(true);
    await act(async () => pending[1]!.resolve("late answer for b"));
    expect(result.current.data).toBeNull();
    await act(async () => pending[2]!.resolve("answer for c"));
    await waitFor(() => expect(result.current.data).toBe("answer for c"));
  });

  it("keeps showing what it has while it reloads, and shows the new answer when it comes", async () => {
    const { load, pending } = controlled<string>();
    const { result } = renderHook(() => useAsync((signal) => load(signal), []));
    await waitFor(() => expect(pending).toHaveLength(1));
    await act(async () => pending[0]!.resolve("first"));
    await waitFor(() => expect(result.current.data).toBe("first"));

    act(() => result.current.reload());
    await waitFor(() => expect(pending).toHaveLength(2));
    expect(result.current.data).toBe("first");
    expect(result.current.loading).toBe(false);

    await act(async () => pending[1]!.resolve("second"));
    await waitFor(() => expect(result.current.data).toBe("second"));
  });

  it("goes back to loading when it is retried after a failure that left nothing on screen", async () => {
    const { load, pending } = controlled<string>();
    const { result } = renderHook(() => useAsync((signal) => load(signal), []));
    await waitFor(() => expect(pending).toHaveLength(1));
    await act(async () => pending[0]!.reject(new Error("down")));
    await waitFor(() => expect(result.current.error).toBe("down"));

    act(() => result.current.reload());
    await waitFor(() => expect(pending).toHaveLength(2));
    expect(result.current.error).toBeNull();
    expect(result.current.loading).toBe(true);
    await act(async () => pending[1]!.resolve("back"));
    await waitFor(() => expect(result.current.data).toBe("back"));
  });

  it("keeps the old data and shows the problem when a reload fails", async () => {
    const { load, pending } = controlled<string>();
    const { result } = renderHook(() => useAsync((signal) => load(signal), []));
    await waitFor(() => expect(pending).toHaveLength(1));
    await act(async () => pending[0]!.resolve("first"));
    await waitFor(() => expect(result.current.data).toBe("first"));
    act(() => result.current.reload());
    await waitFor(() => expect(pending).toHaveLength(2));
    await act(async () => pending[1]!.reject(new Error("down")));
    await waitFor(() => expect(result.current.error).toBe("down"));
    expect(result.current.data).toBe("first");
  });

  it("lets the screen change the data it shows (a change it has just made itself)", async () => {
    const { result } = renderHook(() => useAsync(async () => ["a"], []));
    await waitFor(() => expect(result.current.data).toEqual(["a"]));
    act(() => result.current.setData((old) => [...(old ?? []), "b"]));
    expect(result.current.data).toEqual(["a", "b"]);
  });

  it("does not load while it is not enabled, and loads when it becomes so", async () => {
    const load = vi.fn(async () => "data");
    const { result, rerender } = renderHook(({ on }) => useAsync(load, [], { enabled: on }), { initialProps: { on: false } });
    expect(result.current.loading).toBe(false);
    expect(load).not.toHaveBeenCalled();
    rerender({ on: true });
    await waitFor(() => expect(result.current.data).toBe("data"));
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("cancels what it was loading when the screen goes away", async () => {
    const { load, pending } = controlled<string>();
    const { unmount } = renderHook(() => useAsync((signal) => load(signal), []));
    await waitFor(() => expect(pending).toHaveLength(1));
    unmount();
    expect(pending[0]!.signal.aborted).toBe(true);
  });

  describe("while following something", () => {
    it("loads again every so often until the data says it is final", async () => {
      vi.useFakeTimers();
      let n = 0;
      const load = vi.fn(async () => (n += 1));
      const { result } = renderHook(() => useAsync(load, [], { pollMs: 1000, until: (value) => value >= 3 }));
      await act(async () => vi.advanceTimersByTimeAsync(0));
      expect(result.current.data).toBe(1);
      await act(async () => vi.advanceTimersByTimeAsync(1000));
      expect(result.current.data).toBe(2);
      await act(async () => vi.advanceTimersByTimeAsync(1000));
      expect(result.current.data).toBe(3);
      await act(async () => vi.advanceTimersByTimeAsync(10_000));
      expect(load).toHaveBeenCalledTimes(3);
    });

    it("does not blank the screen between polls", async () => {
      vi.useFakeTimers();
      const seen: (number | null)[] = [];
      let n = 0;
      const { result } = renderHook(() => {
        const state = useAsync(async () => (n += 1), [], { pollMs: 500 });
        seen.push(state.data);
        return state;
      });
      await act(async () => vi.advanceTimersByTimeAsync(2000));
      expect(result.current.data).toBeGreaterThan(1);
      const first = seen.findIndex((value) => value !== null);
      expect(seen.slice(first).every((value) => value !== null)).toBe(true);
    });

    it("tries again, more slowly, after a hiccup that follows a good answer, and keeps what it had", async () => {
      vi.useFakeTimers();
      const answers: (string | Error)[] = ["ok", new Error("blip"), "ok again"];
      const load = vi.fn(async () => {
        const next = answers.shift() ?? "later";
        if (next instanceof Error) throw next;
        return next;
      });
      const { result } = renderHook(() => useAsync(load, [], { pollMs: 1000 }));
      await act(async () => vi.advanceTimersByTimeAsync(0));
      expect(result.current.data).toBe("ok");
      await act(async () => vi.advanceTimersByTimeAsync(1000));
      expect(result.current.error).toBe("blip");
      expect(result.current.data).toBe("ok");
      await act(async () => vi.advanceTimersByTimeAsync(1999));
      expect(load).toHaveBeenCalledTimes(2);
      await act(async () => vi.advanceTimersByTimeAsync(1));
      expect(load).toHaveBeenCalledTimes(3);
      expect(result.current.data).toBe("ok again");
      expect(result.current.error).toBeNull();
    });

    it("stops polling when the screen goes away", async () => {
      vi.useFakeTimers();
      const load = vi.fn(async () => 1);
      const { unmount } = renderHook(() => useAsync(load, [], { pollMs: 1000 }));
      await act(async () => vi.advanceTimersByTimeAsync(0));
      unmount();
      await vi.advanceTimersByTimeAsync(10_000);
      expect(load).toHaveBeenCalledTimes(1);
    });
  });
});
