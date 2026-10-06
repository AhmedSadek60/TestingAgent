import { describe, expect, it } from "vitest";
import { parseFrame, parseSse, type SseFrame } from "./sse";

function streamOf(chunks: (string | Uint8Array)[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(typeof chunk === "string" ? encoder.encode(chunk) : chunk);
      controller.close();
    },
  });
}

async function collect(body: ReadableStream<Uint8Array>): Promise<SseFrame[]> {
  const frames: SseFrame[] = [];
  for await (const frame of parseSse(body)) frames.push(frame);
  return frames;
}

describe("parseFrame", () => {
  it("returns nothing for an empty or blank frame", () => {
    expect(parseFrame("")).toBeNull();
    expect(parseFrame("  \n ")).toBeNull();
  });

  it("reads the event name, id, data and retry", () => {
    expect(parseFrame("event: run\nid: 7\nretry: 2500\ndata: {\"a\":1}")).toEqual({
      event: "run",
      id: "7",
      data: '{"a":1}',
      retry: 2500,
      comment: null,
    });
  });

  it("names the event `message` when none is given", () => {
    expect(parseFrame("data: hi")?.event).toBe("message");
  });

  it("joins several data lines with a newline", () => {
    expect(parseFrame("data: one\ndata: two\ndata:three")?.data).toBe("one\ntwo\nthree");
  });

  it("strips only the single space after the colon", () => {
    expect(parseFrame("data:   padded")?.data).toBe("  padded");
    expect(parseFrame("data:tight")?.data).toBe("tight");
  });

  it("reads a comment (a keep-alive) as a frame with no data", () => {
    expect(parseFrame(": keep-alive")).toMatchObject({ comment: "keep-alive", data: "", event: "message" });
  });

  it("ignores a retry that is not a number and fields it does not know", () => {
    const frame = parseFrame("retry: soon\nflavour: mint\ndata: x");
    expect(frame?.retry).toBeNull();
    expect(frame?.data).toBe("x");
  });

  it("treats a field with no colon as an empty value", () => {
    expect(parseFrame("data")?.data).toBe("");
  });

  it("keeps colons inside the data", () => {
    expect(parseFrame('data: {"t":"12:30:00"}')?.data).toBe('{"t":"12:30:00"}');
  });
});

describe("parseSse", () => {
  const TEXT = [
    "retry: 1500\n\n",
    "id: 1\nevent: run\ndata: {\"n\":1,\"note\":\"café ✓\"}\n\n",
    ": keep-alive\n\n",
    "id: 2\nevent: run\ndata: {\"n\":2}\n\n",
    "event: end\ndata: {\"status\":\"completed\"}\n\n",
  ].join("");

  it("yields every frame in order", async () => {
    const frames = await collect(streamOf([TEXT]));
    expect(frames.map((f) => [f.event, f.id])).toEqual([
      ["message", null],
      ["run", "1"],
      ["message", null],
      ["run", "2"],
      ["end", null],
    ]);
    expect(frames[0]?.retry).toBe(1500);
    expect(frames[1]?.data).toBe('{"n":1,"note":"café ✓"}');
    expect(frames[2]?.comment).toBe("keep-alive");
  });

  it("gives the same frames however the bytes are cut, even in the middle of a character", async () => {
    const expected = await collect(streamOf([TEXT]));
    const bytes = new TextEncoder().encode(TEXT);
    for (let cut = 1; cut < bytes.length; cut += 1) {
      const frames = await collect(streamOf([bytes.slice(0, cut), bytes.slice(cut)]));
      expect(frames).toEqual(expected);
    }
  });

  it("gives the same frames when the bytes arrive one at a time", async () => {
    const expected = await collect(streamOf([TEXT]));
    const bytes = new TextEncoder().encode(TEXT);
    const single = Array.from(bytes, (_, i) => bytes.slice(i, i + 1));
    expect(await collect(streamOf(single))).toEqual(expected);
  });

  it("accepts CRLF line endings", async () => {
    const frames = await collect(streamOf(["id: 1\r\nevent: run\r\ndata: a\r\ndata: b\r\n\r\nid: 2\r\ndata: c\r\n\r\n"]));
    expect(frames.map((f) => [f.id, f.data])).toEqual([
      ["1", "a\nb"],
      ["2", "c"],
    ]);
  });

  it("yields a last frame the server never terminated", async () => {
    const frames = await collect(streamOf(["data: first\n\ndata: second"]));
    expect(frames.map((f) => f.data)).toEqual(["first", "second"]);
  });

  it("yields nothing for an empty stream", async () => {
    expect(await collect(streamOf([]))).toEqual([]);
  });

  it("releases the reader when the consumer stops early", async () => {
    const body = streamOf(["data: 1\n\ndata: 2\n\n"]);
    for await (const frame of parseSse(body)) {
      expect(frame.data).toBe("1");
      break;
    }
    expect(body.locked).toBe(false);
  });
});
