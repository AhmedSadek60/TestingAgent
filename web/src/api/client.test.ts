import { describe, expect, it, vi } from "vitest";
import { ApiClient, withQuery } from "./client";
import { ApiError, NetworkError } from "./errors";
import type { SseFrame } from "./sse";

const TOKEN = "unit-test-token-not-a-secret";

interface Call {
  url: string;
  init: Omit<RequestInit, "headers"> & { headers: Headers };
}
type Step = Response | Error | DOMException | ((call: Call) => Response | Promise<Response>);

/** A `fetch` that answers from a script (the last step repeats) and remembers what it was asked. */
function scripted(...steps: Step[]) {
  const calls: Call[] = [];
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const call = { url: String(input), init: (init ?? {}) as Call["init"] };
    const step = steps[Math.min(calls.length, steps.length - 1)]!;
    calls.push(call);
    if (typeof step === "function") return step(call);
    if (step instanceof Response) return step.clone();
    throw step; // an error of the network (a DOMException is not an `Error` in every realm, so check what it is not)
  }) as typeof fetch;
  return { impl, calls };
}

const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
const failure = (status: number, kind: string, message: string, details: unknown[] = []) => json({ error: { kind, message, details } }, status);
const events = (text: string) => (): Response => new Response(text, { headers: { "Content-Type": "text/event-stream" } });
const END = 'event: end\ndata: {"status":"completed","finished_at":null}\n\n';

function clientFor(steps: Step[], options: { token?: string | null; onUnauthorized?: () => void; base?: string } = {}) {
  const { impl, calls } = scripted(...steps);
  const client = new ApiClient({
    base: options.base,
    getToken: () => (options.token === undefined ? null : options.token),
    onUnauthorized: options.onUnauthorized,
    fetchImpl: impl,
  });
  return { client, calls };
}

describe("withQuery", () => {
  it("returns the path as it is when there is nothing to add", () => {
    expect(withQuery("/runs")).toBe("/runs");
    expect(withQuery("/runs", {})).toBe("/runs");
    expect(withQuery("/runs", { a: undefined, b: null, c: "" })).toBe("/runs");
  });

  it("adds values, turning numbers and booleans into text and keeping zero and false", () => {
    expect(withQuery("/runs", { project: "default", limit: 20, offset: 0, all: false })).toBe("/runs?project=default&limit=20&offset=0&all=false");
  });

  it("repeats the name for a list and skips its empty entries", () => {
    expect(withQuery("/findings", { severity: ["high", "", "critical"] })).toBe("/findings?severity=high&severity=critical");
  });

  it("escapes what must be escaped", () => {
    expect(withQuery("/runs", { q: "a b&c=d" })).toBe("/runs?q=a+b%26c%3Dd");
  });

  it("continues a query the path already has", () => {
    expect(withQuery("/runs?x=1", { y: 2 })).toBe("/runs?x=1&y=2");
  });
});

describe("requests", () => {
  it("sends no Authorization header when there is no token, and the token when there is one", async () => {
    const anonymous = clientFor([json({})]);
    await anonymous.client.get("/things");
    expect(anonymous.calls[0]!.init.headers.get("Authorization")).toBeNull();

    const known = clientFor([json({})], { token: TOKEN });
    await known.client.get("/things");
    expect(known.calls[0]!.init.headers.get("Authorization")).toBe(`Bearer ${TOKEN}`);
  });

  it("reads the token again for every request", async () => {
    let token: string | null = null;
    const { impl, calls } = scripted(json({}));
    const client = new ApiClient({ getToken: () => token, fetchImpl: impl });
    await client.get("/a");
    token = TOKEN;
    await client.get("/b");
    token = null;
    await client.get("/c");
    expect(calls.map((c) => c.init.headers.get("Authorization"))).toEqual([null, `Bearer ${TOKEN}`, null]);
  });

  it("asks for JSON, never reuses a cached answer, and stays on the page's origin", async () => {
    const { client, calls } = clientFor([json({})]);
    await client.get("/things");
    const init = calls[0]!.init;
    expect(init.headers.get("Accept")).toBe("application/json");
    expect(init.cache).toBe("no-store");
    expect(init.credentials).toBe("same-origin");
    expect(init.method).toBe("GET");
    expect(init.body).toBeUndefined();
    expect(init.headers.get("Content-Type")).toBeNull();
  });

  it("puts the base in front of the path, with or without a closing slash", async () => {
    const plain = clientFor([json({})]);
    await plain.client.get("/things", { query: { a: 1 } });
    expect(plain.calls[0]!.url).toBe("/things?a=1");

    const based = clientFor([json({})], { base: "http://127.0.0.1:8765/" });
    await based.client.get("/things");
    expect(based.calls[0]!.url).toBe("http://127.0.0.1:8765/things");
  });

  it("sends a body as JSON and parses the answer", async () => {
    const { client, calls } = clientFor([json({ id: "r1" }, 202)]);
    const answer = await client.post<{ id: string }>("/runs", { target: { kind: "mock" } });
    expect(answer).toEqual({ id: "r1" });
    const init = calls[0]!.init;
    expect(init.method).toBe("POST");
    expect(init.headers.get("Content-Type")).toBe("application/json");
    expect(init.body).toBe('{"target":{"kind":"mock"}}');
  });

  it("sends an empty object when a POST has nothing to say", async () => {
    const { client, calls } = clientFor([json({})]);
    await client.post("/runs/r1/cancel");
    expect(calls[0]!.init.body).toBe("{}");
  });

  it("answers nothing for 204", async () => {
    const { client, calls } = clientFor([new Response(null, { status: 204 })]);
    expect(await client.delete("/credentials/x")).toBeUndefined();
    expect(calls[0]!.init.method).toBe("DELETE");
  });

  it("uploads a form without setting the content type, so the browser can set the boundary", async () => {
    const { client, calls } = clientFor([json({ id: "d1" })], { token: TOKEN });
    const form = new FormData();
    form.append("file", new Blob(["hello"]), "notes.txt");
    expect(await client.upload("/documents", form, { query: { project: "p" } })).toEqual({ id: "d1" });
    const call = calls[0]!;
    expect(call.url).toBe("/documents?project=p");
    expect(call.init.body).toBe(form);
    expect(call.init.headers.get("Content-Type")).toBeNull();
    expect(call.init.headers.get("Authorization")).toBe(`Bearer ${TOKEN}`);
  });
});

describe("failures", () => {
  it("turns the server's error into an ApiError", async () => {
    const { client } = clientFor([failure(422, "validation", "The request is not valid.", [{ field: "name", problem: "required" }])]);
    const error = await client.post("/projects", {}).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 422, kind: "validation", message: "The request is not valid.", details: [{ field: "name", problem: "required" }] });
  });

  it("still gives an ApiError when the body is not the server's error shape", async () => {
    const html = clientFor([new Response("<html>Bad gateway</html>", { status: 502, headers: { "Content-Type": "text/html" } })]);
    const error = await html.client.get("/things").catch((e: unknown) => e);
    expect(error).toMatchObject({ status: 502, kind: "error", message: "The server answered 502.", details: [] });

    const other = clientFor([json({ detail: "something" }, 500)]);
    expect(await other.client.get("/things").catch((e: unknown) => e)).toMatchObject({ status: 500, message: "The server answered 500." });
  });

  it("tells the session when the token is refused, once per refusal", async () => {
    const onUnauthorized = vi.fn();
    const { client } = clientFor([failure(401, "unauthorized", "A token is required.")], { token: TOKEN, onUnauthorized });
    const error = await client.get("/things").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).unauthorized).toBe(true);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("does not take any other refusal for a bad token", async () => {
    const onUnauthorized = vi.fn();
    const { client } = clientFor([failure(403, "forbidden", "Not from this origin.")], { onUnauthorized });
    await expect(client.post("/things", {})).rejects.toMatchObject({ status: 403 });
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("says plainly that the server cannot be reached", async () => {
    const { client } = clientFor([new TypeError("fetch failed")]);
    const error = await client.get("/things").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(NetworkError);
    expect((error as Error).message).toBe("Cannot reach the AgentLab server. Is it running?");
  });

  it("lets an abort through as an abort, not as a network failure", async () => {
    const { client } = clientFor([new DOMException("The operation was aborted.", "AbortError")]);
    const error = await client.get("/things").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(DOMException);
    expect((error as DOMException).name).toBe("AbortError");
  });
});

describe("blob", () => {
  it("reads the file name and type the server gave", async () => {
    const response = new Response("<html></html>", {
      headers: { "Content-Type": "text/html; charset=utf-8", "Content-Disposition": 'attachment; filename="report-v2.html"' },
    });
    const { client, calls } = clientFor([response]);
    const file = await client.blob("/reports/r1/files/html");
    expect(file.name).toBe("report-v2.html");
    expect(file.type).toContain("text/html");
    expect(await file.blob.text()).toBe("<html></html>");
    expect(calls[0]!.init.headers.get("Accept")).toBe("*/*");
  });

  it("reads a file name without quotes, and copes with none", async () => {
    const bare = clientFor([new Response("{}", { headers: { "Content-Disposition": "attachment; filename=run.json" } })]);
    expect((await bare.client.blob("/x")).name).toBe("run.json");

    const none = clientFor([new Response("x")]);
    expect((await none.client.blob("/x")).name).toBeNull();
  });
});

describe("stream", () => {
  async function drain(source: AsyncGenerator<SseFrame>): Promise<SseFrame[]> {
    const frames: SseFrame[] = [];
    for await (const frame of source) frames.push(frame);
    return frames;
  }

  it("reads the events, asking for an event stream and carrying the token", async () => {
    const { client, calls } = clientFor([events("id: 1\ndata: a\n\nid: 2\ndata: b\n\n")], { token: TOKEN });
    const frames = await drain(client.stream("/runs/r1/events"));
    expect(frames.map((f) => f.data)).toEqual(["a", "b"]);
    const headers = calls[0]!.init.headers;
    expect(headers.get("Accept")).toBe("text/event-stream");
    expect(headers.get("Authorization")).toBe(`Bearer ${TOKEN}`);
    expect(headers.get("Last-Event-ID")).toBeNull();
  });

  it("resumes after the event it was given", async () => {
    const { client, calls } = clientFor([events("")]);
    await drain(client.stream("/runs/r1/events", { after: "41" }));
    expect(calls[0]!.init.headers.get("Last-Event-ID")).toBe("41");
  });

  it("refuses an answer that has no stream in it", async () => {
    const { client } = clientFor([new Response(null, { status: 200 })]);
    await expect(drain(client.stream("/runs/r1/events"))).rejects.toThrow("The server sent no event stream.");
  });
});

describe("follow", () => {
  function follow(client: ApiClient, extra: { retryMs?: number; maxAttempts?: number; signal?: AbortSignal } = {}) {
    const frames: SseFrame[] = [];
    const connection: boolean[] = [];
    const done = client.follow("/runs/r1/events", {
      retryMs: 1,
      ...extra,
      onFrame: (frame) => frames.push(frame),
      onConnection: (connected) => connection.push(connected),
    });
    return { done, frames, connection };
  }

  it("hands over every event and stops at the end event", async () => {
    const { client } = clientFor([events(`id: 1\ndata: {"n":1}\n\nid: 2\ndata: {"n":2}\n\n${END}`)]);
    const run = follow(client);
    await run.done;
    expect(run.frames.map((f) => f.event)).toEqual(["message", "message", "end"]);
    expect(run.connection).toEqual([true, false]);
  });

  it("does not pass on the server's keep-alive comments, but counts them as a live connection", async () => {
    const { client } = clientFor([events(`: keep-alive\n\nid: 1\ndata: {"n":1}\n\n${END}`)]);
    const run = follow(client);
    await run.done;
    expect(run.frames.map((f) => f.data)).toEqual(['{"n":1}', '{"status":"completed","finished_at":null}']);
    expect(run.connection[0]).toBe(true);
  });

  it("reconnects from the last event it saw when the stream stops without an end", async () => {
    const { client, calls } = clientFor([events("id: 1\ndata: a\n\nid: 2\ndata: b\n\n"), events(`id: 3\ndata: c\n\n${END}`)]);
    const run = follow(client);
    await run.done;
    expect(run.frames.filter((f) => f.event !== "end").map((f) => f.data)).toEqual(["a", "b", "c"]);
    expect(calls).toHaveLength(2);
    expect(calls[0]!.init.headers.get("Last-Event-ID")).toBeNull();
    expect(calls[1]!.init.headers.get("Last-Event-ID")).toBe("2");
  });

  it("retries a server error and reports the connection going down and coming back", async () => {
    const { client, calls } = clientFor([failure(503, "unavailable", "Busy."), events(`id: 1\ndata: a\n\n${END}`)]);
    const run = follow(client);
    await run.done;
    expect(calls).toHaveLength(2);
    expect(run.connection).toEqual([false, true, false]);
    expect(run.frames.map((f) => f.data)[0]).toBe("a");
  });

  it("retries when the server cannot be reached", async () => {
    const { client, calls } = clientFor([new TypeError("fetch failed"), events(`id: 1\ndata: a\n\n${END}`)]);
    const run = follow(client);
    await run.done;
    expect(calls).toHaveLength(2);
    expect(run.connection).toEqual([false, true, false]);
  });

  it("does not retry a refusal", async () => {
    const { client, calls } = clientFor([failure(404, "not_found", "No such run.")]);
    const run = follow(client);
    await expect(run.done).rejects.toMatchObject({ status: 404, message: "No such run." });
    expect(calls).toHaveLength(1);
  });

  it("asks for the token again when it is refused", async () => {
    const onUnauthorized = vi.fn();
    const { client, calls } = clientFor([failure(401, "unauthorized", "A token is required.")], { onUnauthorized });
    await expect(follow(client).done).rejects.toMatchObject({ status: 401 });
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    expect(calls).toHaveLength(1);
  });

  it("gives up after the attempts it was allowed", async () => {
    const { client, calls } = clientFor([failure(503, "unavailable", "Busy.")]);
    const run = follow(client, { maxAttempts: 3 });
    await expect(run.done).rejects.toMatchObject({ status: 503 });
    expect(calls).toHaveLength(3);
    expect(run.connection).toEqual([false, false, false]);
  });

  it("waits as long as the server's retry hint says, not as long as it was told to by default", async () => {
    const { client, calls } = clientFor([events("retry: 1\nid: 1\ndata: a\n\n"), events(`id: 2\ndata: b\n\n${END}`)]);
    const run = follow(client, { retryMs: 60_000 });
    await run.done; // would take a minute if the hint were ignored
    expect(calls).toHaveLength(2);
  });

  it("stops at once when asked to while a connection is being made", async () => {
    const hanging: Step = (call) =>
      new Promise<Response>((_, reject) => {
        call.init.signal?.addEventListener("abort", () => reject(new DOMException("The operation was aborted.", "AbortError")));
      });
    const { client } = clientFor([hanging]);
    const controller = new AbortController();
    const run = follow(client, { signal: controller.signal });
    controller.abort();
    await run.done;
    expect(run.frames).toEqual([]);
  });

  it("stops waiting to reconnect when asked to", async () => {
    const { client, calls } = clientFor([events("id: 1\ndata: a\n\n")]);
    const controller = new AbortController();
    const run = follow(client, { signal: controller.signal, retryMs: 60_000 });
    await vi.waitFor(() => expect(run.frames).toHaveLength(1));
    controller.abort();
    await run.done;
    expect(calls).toHaveLength(1);
  });

  it("does not wait to reconnect when it was asked to stop while reading the last event", async () => {
    const { client, calls } = clientFor([events("id: 1\ndata: a\n\n")]);
    const controller = new AbortController();
    const frames: SseFrame[] = [];
    await client.follow("/runs/r1/events", {
      signal: controller.signal,
      retryMs: 60_000,
      onFrame: (frame) => {
        frames.push(frame);
        controller.abort();
      },
    });
    expect(frames).toHaveLength(1);
    expect(calls).toHaveLength(1);
  });

  it("does not even connect when it was told to stop first", async () => {
    const { client, calls } = clientFor([events(END)]);
    const controller = new AbortController();
    controller.abort();
    await follow(client, { signal: controller.signal }).done;
    expect(calls).toHaveLength(0);
  });
});
