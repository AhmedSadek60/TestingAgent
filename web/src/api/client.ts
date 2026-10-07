import { ApiError, NetworkError } from "./errors";
import { parseSse, type SseFrame } from "./sse";

type QueryValue = string | number | boolean | null | undefined;
export type Query = Record<string, QueryValue | QueryValue[]>;

export interface ClientOptions {
  /** Where the API is. The interface is served by the API itself, so the default is the page's own origin. */
  base?: string;
  /** The API token, or `null` when the server does not ask for one. Read on every call, never stored here. */
  getToken: () => string | null;
  /** Called when the server says the token is missing or wrong. */
  onUnauthorized?: () => void;
  fetchImpl?: typeof fetch;
}

export interface RequestOptions {
  query?: Query;
  body?: unknown;
  signal?: AbortSignal;
  headers?: Record<string, string>;
}

export function withQuery(path: string, query?: Query): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    for (const item of Array.isArray(value) ? value : [value]) {
      if (item !== undefined && item !== null && item !== "") params.append(key, String(item));
    }
  }
  const text = params.toString();
  return text ? `${path}${path.includes("?") ? "&" : "?"}${text}` : path;
}

export class ApiClient {
  private readonly base: string;
  private readonly getToken: () => string | null;
  private readonly onUnauthorized: () => void;
  private readonly fetchImpl: typeof fetch;

  constructor(options: ClientOptions) {
    this.base = (options.base ?? "").replace(/\/$/, "");
    this.getToken = options.getToken;
    this.onUnauthorized = options.onUnauthorized ?? (() => undefined);
    this.fetchImpl = options.fetchImpl ?? ((...args) => fetch(...args));
  }

  private headers(extra?: Record<string, string>, json = false): Headers {
    const headers = new Headers(extra);
    const token = this.getToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    if (json) headers.set("Content-Type", "application/json");
    headers.set("Accept", headers.get("Accept") ?? "application/json");
    return headers;
  }

  private async send(method: string, path: string, options: RequestOptions, raw: BodyInit | null = null): Promise<Response> {
    const hasJson = options.body !== undefined && raw === null;
    let response: Response;
    try {
      response = await this.fetchImpl(this.base + withQuery(path, options.query), {
        method,
        headers: this.headers(options.headers, hasJson),
        body: raw ?? (hasJson ? JSON.stringify(options.body) : undefined),
        signal: options.signal,
        credentials: "same-origin",
        cache: "no-store",
      });
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") throw error;
      throw new NetworkError("Cannot reach the AgentLab server. Is it running?");
    }
    if (!response.ok) throw await this.failure(response);
    return response;
  }

  private async failure(response: Response): Promise<ApiError> {
    let kind = "error";
    let message = `The server answered ${response.status}.`;
    let details: { field: string; problem: string }[] = [];
    try {
      const body = (await response.json()) as { error?: { kind?: string; message?: string; details?: typeof details } };
      if (body.error) {
        kind = body.error.kind ?? kind;
        message = body.error.message ?? message;
        details = body.error.details ?? [];
      }
    } catch {
      /* the body was not the server's error shape; the status says enough */
    }
    if (response.status === 401) this.onUnauthorized();
    return new ApiError(response.status, kind, message, details);
  }

  async request<T>(method: string, path: string, options: RequestOptions = {}): Promise<T> {
    const response = await this.send(method, path, options);
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  get<T>(path: string, options?: RequestOptions): Promise<T> {
    return this.request<T>("GET", path, options);
  }

  post<T>(path: string, body?: unknown, options: RequestOptions = {}): Promise<T> {
    return this.request<T>("POST", path, { ...options, body: body ?? {} });
  }

  delete<T>(path: string, options?: RequestOptions): Promise<T> {
    return this.request<T>("DELETE", path, options);
  }

  /** Multipart upload (the browser sets the boundary). */
  async upload<T>(path: string, form: FormData, options: RequestOptions = {}): Promise<T> {
    const response = await this.send("POST", path, options, form);
    return (await response.json()) as T;
  }

  /** A file: a report, an artifact. */
  async blob(path: string, options: RequestOptions = {}): Promise<{ blob: Blob; type: string; name: string | null }> {
    const response = await this.send("GET", path, { ...options, headers: { Accept: "*/*", ...options.headers } });
    const disposition = response.headers.get("content-disposition") ?? "";
    const name = /filename="?([^";]+)"?/i.exec(disposition)?.[1] ?? null;
    return { blob: await response.blob(), type: response.headers.get("content-type") ?? "", name };
  }

  /** One connection to an event stream: yields frames until the server ends it. */
  async *stream(path: string, options: { after?: string | null; signal?: AbortSignal } = {}): AsyncGenerator<SseFrame> {
    const headers: Record<string, string> = { Accept: "text/event-stream" };
    if (options.after) headers["Last-Event-ID"] = options.after;
    const response = await this.send("GET", path, { headers, signal: options.signal });
    if (!response.body) throw new NetworkError("The server sent no event stream.");
    yield* parseSse(response.body);
  }

  /**
   * Follow a run's events until the server says it is over, reconnecting from the last event seen when the connection drops.
   * Resolves when the stream ended normally (or `signal` aborted); rejects on an error that retrying cannot fix.
   */
  async follow(
    path: string,
    handlers: {
      onFrame: (frame: SseFrame) => void;
      onConnection?: (connected: boolean) => void;
      signal?: AbortSignal;
      /** Pause before reconnecting, in ms (default 1500; the server's `retry:` hint wins). */
      retryMs?: number;
      maxAttempts?: number;
    },
  ): Promise<void> {
    let after: string | null = null;
    let retry = handlers.retryMs ?? 1500;
    let failures = 0;
    const maxAttempts = handlers.maxAttempts ?? 30;
    for (;;) {
      if (handlers.signal?.aborted) return;
      try {
        for await (const frame of this.stream(path, { after, signal: handlers.signal })) {
          if (failures > 0 || after === null) handlers.onConnection?.(true);
          failures = 0;
          if (frame.retry !== null) retry = frame.retry;
          if (frame.id) after = frame.id;
          if (frame.event === "end") {
            handlers.onFrame(frame);
            handlers.onConnection?.(false);
            return;
          }
          if (frame.comment === null || frame.data) handlers.onFrame(frame);
        }
      } catch (error) {
        if (handlers.signal?.aborted || (error instanceof DOMException && error.name === "AbortError")) return;
        if (error instanceof ApiError && error.status < 500) throw error; // a refusal is not going to change by waiting
        failures += 1;
        handlers.onConnection?.(false);
        if (failures >= maxAttempts) throw error;
      }
      await pause(retry, handlers.signal);
    }
  }
}

/** Wait `ms`, or less if `signal` aborts (including when it already has: an abort that came earlier must not cost the wait). */
function pause(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise<void>((resolve) => {
    if (signal?.aborted) {
      resolve();
      return;
    }
    const done = () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", done);
      resolve();
    };
    const timer = setTimeout(done, ms);
    signal?.addEventListener("abort", done, { once: true });
  });
}
