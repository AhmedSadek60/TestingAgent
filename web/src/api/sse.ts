/** Server-sent events read with `fetch`, because `EventSource` cannot send the Authorization header. */

export interface SseFrame {
  event: string;
  id: string | null;
  data: string;
  retry: number | null;
  comment: string | null;
}

/** Split a byte stream into events. Handles a frame cut at any byte, CRLF, comments (`: keep-alive`) and multi-line data. */
export async function* parseSse(body: ReadableStream<Uint8Array>): AsyncGenerator<SseFrame> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      for (;;) {
        const boundary = /\r?\n\r?\n/.exec(buffer);
        if (!boundary) break;
        const raw = buffer.slice(0, boundary.index);
        buffer = buffer.slice(boundary.index + boundary[0].length);
        const frame = parseFrame(raw);
        if (frame) yield frame;
      }
    }
    buffer += decoder.decode();
    const last = parseFrame(buffer.trim());
    if (last) yield last;
  } finally {
    reader.releaseLock();
  }
}

export function parseFrame(raw: string): SseFrame | null {
  if (!raw.trim()) return null;
  const frame: SseFrame = { event: "message", id: null, data: "", retry: null, comment: null };
  const data: string[] = [];
  for (const line of raw.split(/\r?\n/)) {
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    switch (field) {
      case "":
        frame.comment = value;
        break;
      case "event":
        frame.event = value;
        break;
      case "id":
        frame.id = value;
        break;
      case "data":
        data.push(value);
        break;
      case "retry": {
        const n = Number.parseInt(value, 10);
        if (Number.isFinite(n)) frame.retry = n;
        break;
      }
      default:
        break;
    }
  }
  frame.data = data.join("\n");
  return frame;
}
