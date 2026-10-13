/** A stand-in for the AgentLab server, for tests of screens that talk to it: answers by route, remembers every call, and
 * refuses what it was not told about (so a screen cannot quietly call something the test did not expect). */
import { render, type RenderResult } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { ToastProvider } from "../components/ui";
import { SessionProvider } from "../session";

export interface RecordedCall {
  method: string;
  path: string;
  query: URLSearchParams;
  headers: Headers;
  body: unknown;
}

/** What a route answers: a `Response`, or any value (sent as JSON with status 200). Not `unknown`, which would swallow the
 * function type and leave a handler's argument without a type. */
type Answer = object | string | number | boolean | null;
export type Handler = Answer | ((call: RecordedCall) => Answer | Promise<Answer>);

export const jsonResponse = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

export const errorResponse = (status: number, kind: string, message: string): Response => jsonResponse({ error: { kind, message, details: [] } }, status);

export const HEALTH_OPEN = { status: "ok", version: "0.0.0-test", auth_required: false };
export const HEALTH_PROTECTED = { ...HEALTH_OPEN, auth_required: true };
export const project = (name: string) => ({ id: `id-${name}`, name, description: "", objective: "", created_at: "2026-10-06T10:00:00Z" });

export function fakeServer(routes: Record<string, Handler>) {
  const calls: RecordedCall[] = [];
  const table = new Map(Object.entries(routes));
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = new URL(String(input), "http://localhost");
    const method = (init?.method ?? "GET").toUpperCase();
    let body: unknown = undefined;
    if (typeof init?.body === "string") {
      try {
        body = JSON.parse(init.body);
      } catch {
        body = init.body;
      }
    } else if (init?.body instanceof FormData) {
      body = init.body; // an upload: the test reads the parts it sent
    }
    const call: RecordedCall = { method, path: url.pathname, query: url.searchParams, headers: new Headers(init?.headers), body };
    calls.push(call);
    const handler = table.get(`${method} ${url.pathname}`);
    if (handler === undefined) return errorResponse(404, "not_found", `The test server has no route for ${method} ${url.pathname}.`);
    const answer = typeof handler === "function" ? await handler(call) : handler;
    return answer instanceof Response ? answer.clone() : jsonResponse(answer);
  });
  vi.stubGlobal("fetch", fetchMock);
  return {
    calls,
    /** The calls made to one route, oldest first. */
    to: (route: string) => calls.filter((c) => `${c.method} ${c.path}` === route),
    /** Change what a route answers from now on. */
    set: (route: string, handler: Handler) => void table.set(route, handler),
  };
}

/** Render something that needs the session (the API client, the project) and the toasts, at a given route. */
export function renderWithSession(ui: ReactElement, { route = "/" }: { route?: string } = {}): RenderResult {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <ToastProvider>
        <SessionProvider>{ui}</SessionProvider>
      </ToastProvider>
    </MemoryRouter>,
  );
}

/** XMLHttpRequest for tests (jsdom's would try the network): what is posted goes through the fake server's `fetch`, and the
 * upload reports its progress once, half way and at the end, as a browser would. */
export function installFakeXhr(): void {
  class FakeXhr {
    upload: { onprogress: ((event: { lengthComputable: boolean; loaded: number; total: number }) => void) | null } = { onprogress: null };
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    onabort: (() => void) | null = null;
    status = 0;
    responseText = "";
    responseType = "";
    private method = "GET";
    private url = "";
    private readonly headers = new Headers();
    private responseHeaders: Headers | null = null;
    private stopped = false;

    open(method: string, url: string): void {
      this.method = method;
      this.url = url;
    }
    setRequestHeader(name: string, value: string): void {
      this.headers.set(name, value);
    }
    getResponseHeader(name: string): string | null {
      return this.responseHeaders?.get(name) ?? null;
    }
    abort(): void {
      this.stopped = true;
      this.onabort?.();
    }
    send(body: FormData): void {
      void (async () => {
        let total = 0;
        body.forEach((value) => {
          total += typeof value === "string" ? value.length : value.size;
        });
        this.upload.onprogress?.({ lengthComputable: true, loaded: Math.floor(total / 2), total });
        try {
          const response = await fetch(this.url, { method: this.method, headers: this.headers, body });
          if (this.stopped) return;
          this.status = response.status;
          this.responseText = await response.text();
          this.responseHeaders = response.headers;
          this.upload.onprogress?.({ lengthComputable: true, loaded: total, total });
          this.onload?.();
        } catch {
          if (!this.stopped) this.onerror?.();
        }
      })();
    }
  }
  vi.stubGlobal("XMLHttpRequest", FakeXhr);
}
