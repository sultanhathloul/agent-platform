// Server-Sent Events parser following the WHATWG event-stream rules
// (https://html.spec.whatwg.org/multipage/server-sent-events.html#event-stream-interpretation).
// fetch() is used instead of EventSource because the gateway needs an
// Authorization header and a POST body.

export type SseMessage = {
  event: string;
  data: string;
  id?: string;
  retry?: number;
};

export class SseParser {
  private buffer = "";
  private pendingCR = false;
  private dataLines: string[] = [];
  private eventType = "";
  private lastEventId: string | undefined;
  private retry: number | undefined;

  /** Feeds decoded text; returns every event completed by this chunk. */
  feed(chunk: string): SseMessage[] {
    const out: SseMessage[] = [];
    let text = chunk;
    // A CR ending the previous chunk may be the first half of a CRLF.
    if (this.pendingCR) {
      this.pendingCR = false;
      if (text.startsWith("\n")) text = text.slice(1);
    }
    const buf = this.buffer + text;
    let start = 0;
    for (let i = 0; i < buf.length; i++) {
      const c = buf[i];
      if (c !== "\n" && c !== "\r") continue;
      const line = buf.slice(start, i);
      if (c === "\r") {
        if (i + 1 < buf.length) {
          if (buf[i + 1] === "\n") i++;
        } else {
          this.pendingCR = true;
        }
      }
      this.processLine(line, out);
      start = i + 1;
    }
    this.buffer = buf.slice(start);
    return out;
  }

  /** Ends the stream: a trailing event without a final blank line is still delivered. */
  flush(): SseMessage[] {
    const out: SseMessage[] = [];
    if (this.buffer.length > 0) {
      this.processLine(this.buffer, out);
      this.buffer = "";
    }
    this.dispatch(out);
    this.pendingCR = false;
    return out;
  }

  private processLine(line: string, out: SseMessage[]): void {
    if (line === "") {
      this.dispatch(out);
      return;
    }
    if (line.startsWith(":")) return; // comment / keep-alive
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    switch (field) {
      case "event":
        this.eventType = value;
        break;
      case "data":
        this.dataLines.push(value);
        break;
      case "id":
        if (!value.includes("\0")) this.lastEventId = value;
        break;
      case "retry":
        if (/^\d+$/.test(value)) this.retry = Number(value);
        break;
      default:
        break; // unknown fields are ignored
    }
  }

  private dispatch(out: SseMessage[]): void {
    if (this.dataLines.length === 0) {
      this.eventType = "";
      return;
    }
    const message: SseMessage = { event: this.eventType || "message", data: this.dataLines.join("\n") };
    if (this.lastEventId !== undefined) message.id = this.lastEventId;
    if (this.retry !== undefined) message.retry = this.retry;
    out.push(message);
    this.dataLines = [];
    this.eventType = "";
  }
}

/** Decodes a byte stream as UTF-8 (safe across split multi-byte characters) and yields SSE messages. */
export async function* readSse(stream: ReadableStream<Uint8Array>): AsyncGenerator<SseMessage> {
  const reader = stream.getReader();
  const decoder = new TextDecoder("utf-8");
  const parser = new SseParser();
  let finished = false;
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      yield* parser.feed(decoder.decode(value, { stream: true }));
    }
    finished = true;
    yield* parser.feed(decoder.decode());
    yield* parser.flush();
  } finally {
    if (!finished) await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
