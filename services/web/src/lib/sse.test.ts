import { describe, expect, it } from "vitest";
import { SseParser, readSse, type SseMessage } from "./sse";

function parseAll(chunks: string[]): SseMessage[] {
  const parser = new SseParser();
  const out = chunks.flatMap((c) => parser.feed(c));
  return [...out, ...parser.flush()];
}

function byteStream(bytes: Uint8Array, splits: number[]): ReadableStream<Uint8Array> {
  const cuts = [0, ...splits, bytes.length];
  return new ReadableStream({
    start(controller) {
      for (let i = 0; i < cuts.length - 1; i++) controller.enqueue(bytes.slice(cuts[i], cuts[i + 1]));
      controller.close();
    },
  });
}

async function collect(stream: ReadableStream<Uint8Array>): Promise<SseMessage[]> {
  const out: SseMessage[] = [];
  for await (const m of readSse(stream)) out.push(m);
  return out;
}

const PAYLOAD =
  'event: token\nid: 1\ndata: {"type":"token","data":{"text":"مرحبًا"}}\n\n' +
  ": keep-alive\n\n" +
  'event: done\nid: 2\ndata: {"type":"done",\ndata: "answer":"تم"}\n\n';

const EXPECTED: SseMessage[] = [
  { event: "token", id: "1", data: '{"type":"token","data":{"text":"مرحبًا"}}' },
  { event: "done", id: "2", data: '{"type":"done",\n"answer":"تم"}' },
];

describe("SseParser", () => {
  it("parses event, id and data fields", () => {
    expect(parseAll([PAYLOAD])).toEqual(EXPECTED);
  });

  it("gives the same result for every split point of the text", () => {
    for (let i = 0; i <= PAYLOAD.length; i++) {
      expect(parseAll([PAYLOAD.slice(0, i), PAYLOAD.slice(i)])).toEqual(EXPECTED);
    }
  });

  it("accepts CRLF and bare CR line endings", () => {
    expect(parseAll([PAYLOAD.replace(/\n/g, "\r\n")])).toEqual(EXPECTED);
    expect(parseAll([PAYLOAD.replace(/\n/g, "\r")])).toEqual(EXPECTED);
  });

  it("does not create an empty line when CRLF is split between chunks", () => {
    const crlf = PAYLOAD.replace(/\n/g, "\r\n");
    for (let i = 0; i <= crlf.length; i++) {
      expect(parseAll([crlf.slice(0, i), crlf.slice(i)])).toEqual(EXPECTED);
    }
  });

  it("joins multi-line data with newlines", () => {
    expect(parseAll(["data: a\ndata:b\ndata:  c\n\n"])).toEqual([{ event: "message", data: "a\nb\n c" }]);
  });

  it("ignores comments and blank-only blocks", () => {
    expect(parseAll([": comment\n\n\n: another\n"])).toEqual([]);
  });

  it("delivers a trailing event without a final blank line", () => {
    expect(parseAll(["event: done\ndata: {}"])).toEqual([{ event: "done", data: "{}" }]);
    expect(parseAll(["event: done\ndata: {}\n"])).toEqual([{ event: "done", data: "{}" }]);
  });

  it("keeps the last id across events and parses retry", () => {
    expect(parseAll(["id: 7\nretry: 1500\ndata: x\n\ndata: y\n\n"])).toEqual([
      { event: "message", id: "7", retry: 1500, data: "x" },
      { event: "message", id: "7", retry: 1500, data: "y" },
    ]);
  });

  it("treats a field without a colon as an empty value", () => {
    expect(parseAll(["data\n\n"])).toEqual([{ event: "message", data: "" }]);
  });
});

describe("readSse", () => {
  const bytes = new TextEncoder().encode(PAYLOAD);

  it("decodes Arabic text split inside multi-byte UTF-8 sequences", async () => {
    for (let i = 1; i < bytes.length; i++) {
      expect(await collect(byteStream(bytes, [i]))).toEqual(EXPECTED);
    }
  });

  it("handles one byte per chunk", async () => {
    const splits = Array.from({ length: bytes.length - 1 }, (_, i) => i + 1);
    expect(await collect(byteStream(bytes, splits))).toEqual(EXPECTED);
  });
});
