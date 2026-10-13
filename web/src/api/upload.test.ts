import { describe, expect, it, vi } from "vitest";
import { ApiClient } from "./client";
import { ApiError } from "./errors";
import { errorResponse, fakeServer, installFakeXhr, jsonResponse } from "../test/server";

const form = () => {
  const data = new FormData();
  data.append("file", new File(["hello world"], "note.txt", { type: "text/plain" }));
  return data;
};

describe("uploading with progress", () => {
  it("reports the bytes as the browser does and returns the server's answer, with the CSRF token of a session", async () => {
    installFakeXhr();
    const server = fakeServer({ "POST /submissions/s1/attachments": () => jsonResponse({ id: "a1", name: "note.txt" }, 201) });
    const client = new ApiClient({ getToken: () => null, getCsrf: () => "csrf-1" });
    const progress: [number, number][] = [];
    const result = await client.upload<{ id: string }>("/submissions/s1/attachments", form(), { onProgress: (l, t) => progress.push([l, t]) });
    expect(result.id).toBe("a1");
    expect(progress.length).toBeGreaterThanOrEqual(2);
    expect(progress.at(-1)![0]).toBe(progress.at(-1)![1]);
    expect(server.to("POST /submissions/s1/attachments")[0]!.headers.get("X-CSRF-Token")).toBe("csrf-1");
  });

  it("says what the server refused with, and tells the page about the failed request without its body", async () => {
    installFakeXhr();
    fakeServer({ "POST /submissions/s1/attachments": () => errorResponse(422, "invalid_request", "this type of file is not accepted") });
    const failures: unknown[] = [];
    const client = new ApiClient({ getToken: () => "t", onFailure: (f) => failures.push(f) });
    const error = await client.upload("/submissions/s1/attachments", form(), { onProgress: () => undefined }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).message).toBe("this type of file is not accepted");
    expect(failures).toEqual([expect.objectContaining({ method: "POST", path: "/submissions/s1/attachments", status: 422 })]);
  });

  it("reports an unreachable server as such", async () => {
    installFakeXhr();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("failed to fetch");
      }),
    );
    const failures: unknown[] = [];
    const client = new ApiClient({ getToken: () => null, onFailure: (f) => failures.push(f) });
    await expect(client.upload("/x", form(), { onProgress: () => undefined })).rejects.toThrow(/Cannot reach the AgentLab server/);
    expect(failures).toEqual([expect.objectContaining({ path: "/x", status: null, error: "network error" })]);
  });

  it("stops when the page cancels it, and says it was cancelled and not that it failed", async () => {
    installFakeXhr();
    fakeServer({ "POST /y": () => jsonResponse({ id: "never" }) });
    const client = new ApiClient({ getToken: () => null });
    const controller = new AbortController();
    const pending = client.upload("/y", form(), { onProgress: () => undefined, signal: controller.signal });
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });

  it("uses fetch when nobody asked for progress", async () => {
    const server = fakeServer({ "POST /documents": () => jsonResponse({ id: "d1" }) });
    const client = new ApiClient({ getToken: () => "tok" });
    expect(await client.upload<{ id: string }>("/documents", form())).toEqual({ id: "d1" });
    expect(server.calls).toHaveLength(1);
    expect(server.calls[0]!.body).toBeInstanceOf(FormData);
  });
});
