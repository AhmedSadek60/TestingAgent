import { afterEach, describe, expect, it } from "vitest";
import { browserCounts, browserSnapshot, clearBrowserBuffer, recordConsole, recordFailure, startBrowserErrorBuffer } from "./browserErrors";

afterEach(() => clearBrowserBuffer());

describe("what the tab keeps of what went wrong", () => {
  it("records a failed request without its query or its body, and only the server's own request ids", () => {
    recordFailure({ method: "post", path: "/submissions/similar?title=my%20secret", status: 500, error: null, requestId: "abcd1234efgh5678", durationMs: 12.4 });
    recordFailure({ method: "GET", path: "/runs", status: null, error: "network error", requestId: "not a request id!", durationMs: 3 });
    const network = browserSnapshot().network!;
    expect(network).toHaveLength(2);
    expect(network[0]).toMatchObject({ method: "POST", url: "/submissions/similar", status: 500, request_id: "abcd1234efgh5678", duration_ms: 12 });
    expect(JSON.stringify(network)).not.toContain("secret");
    expect(network[1]!.request_id).toBeUndefined();
    expect(network[1]).toMatchObject({ status: null, error: "network error" });
  });

  it("holds a bounded number of entries, newest last, and each message is cut", () => {
    for (let i = 0; i < 80; i += 1) recordConsole("error", [`problem ${i}`]);
    recordConsole("warn", ["x".repeat(5000)]);
    const entries = browserSnapshot().console!;
    expect(entries).toHaveLength(50);
    expect(entries[0]!.message).toBe("problem 31");
    expect(entries[49]!.message).toHaveLength(1500);
    expect(browserCounts()).toEqual({ console: 50, network: 0 });
  });

  it("listens to the console, to uncaught errors and to rejections, and passes the console call on", () => {
    const original = console.error;
    const seen: unknown[][] = [];
    console.error = (...values: unknown[]) => void seen.push(values);
    const stop = startBrowserErrorBuffer();
    try {
      console.error("the page said", { code: 7 });
      window.dispatchEvent(new ErrorEvent("error", { message: "boom", filename: "app.js", lineno: 12 }));
      const entries = browserSnapshot().console!;
      expect(entries.map((e) => e.message)).toEqual(['the page said {"code":7}', "boom"]);
      expect(entries[1]).toMatchObject({ source: "app.js", line: 12, level: "error" });
      expect(seen).toHaveLength(1);
    } finally {
      stop();
      console.error = original;
    }
  });

  it("starts once however often it is asked", () => {
    const stop = startBrowserErrorBuffer();
    const again = startBrowserErrorBuffer();
    try {
      console.warn("one");
      expect(browserSnapshot().console!.filter((e) => e.message === "one")).toHaveLength(1);
    } finally {
      again();
      stop();
    }
  });
});
