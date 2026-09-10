import { describe, expect, it } from "vitest";
import type { StreamEvent } from "@/lib/api/types";
import { resolveErrorMessage } from "./errors";
import { applyEvent, finalizeStream, initialAssistant, markAborted, type AssistantMessage } from "./stream";

const meta = { request_id: "req-1", turn_id: "turn-1" };
let seq = 0;
const next = () => seq++;

const token = (text: string): StreamEvent => ({ ...meta, seq: next(), type: "token", data: { text } });
const done = (answer: string): StreamEvent => ({
  ...meta,
  seq: next(),
  type: "done",
  data: {
    answer,
    citations: [{ id: "c1", title: "دليل الطالب", source: "kb", kb: "moe", url: null, locator: "ص 4" }],
    usage: { tokens_in: 120, tokens_out: 42, cost_usd: 0 },
    guard_events: [],
    state_digest: { turn: 1, capabilities: ["general"] },
    trace_id: "0af7651916cd43dd8448eb211c80319c",
  },
});
const error = (code: string, message_ar = "رسالة الخادم", message_en = "server message"): StreamEvent => ({
  ...meta,
  seq: next(),
  type: "error",
  data: { code, message_ar, message_en, retryable: true },
});

const run = (events: StreamEvent[]): AssistantMessage => events.reduce(applyEvent, initialAssistant());

describe("applyEvent", () => {
  it("accumulates tokens, including Arabic text", () => {
    const m = run([token("الرياض "), token("هي "), token("العاصمة.")]);
    expect(m.text).toBe("الرياض هي العاصمة.");
    expect(m.status).toBe("streaming");
    expect(m.requestId).toBe("req-1");
  });

  it("keeps streamed text on done and records usage, citations and trace id", () => {
    const m = run([token("نص "), token("مبثوث"), done("إجابة نهائية مختلفة")]);
    expect(m.text).toBe("نص مبثوث");
    expect(m.status).toBe("done");
    expect(m.usage).toEqual({ tokens_in: 120, tokens_out: 42, cost_usd: 0 });
    expect(m.citations.map((c) => c.id)).toEqual(["c1"]);
    expect(m.traceId).toBe("0af7651916cd43dd8448eb211c80319c");
  });

  it("uses the done answer when no tokens arrived", () => {
    const m = run([done("الإجابة الكاملة")]);
    expect(m.text).toBe("الإجابة الكاملة");
    expect(m.status).toBe("done");
  });

  it("updates a tool chip in place from start to end", () => {
    const m = run([
      { ...meta, seq: next(), type: "tool_start", data: { call_id: "k1", tool: "web_search", capability: "web" } },
      {
        ...meta,
        seq: next(),
        type: "tool_end",
        data: { call_id: "k1", tool: "web_search", capability: "web", status: "ok", latency_ms: 812 },
      },
    ]);
    expect(m.chips).toEqual([
      { kind: "tool", key: "tool-k1", callId: "k1", tool: "web_search", capability: "web", status: "ok", latencyMs: 812 },
    ]);
  });

  it("adds tier and guard chips", () => {
    const m = run([
      { ...meta, seq: 50, type: "tier", data: { role: "reasoning", step: "answer", tier: "fast", reason: "floor:general" } },
      { ...meta, seq: 51, type: "guard", data: { checkpoint: "OUTPUT", policy_id: "pii-redact-output", action: "redact" } },
    ]);
    expect(m.chips.map((c) => c.kind)).toEqual(["tier", "guard"]);
    expect(m.chips[1]).toMatchObject({ policyId: "pii-redact-output", action: "redact", detail: null });
  });

  it("does not duplicate citations sent both as events and in done", () => {
    const m = run([
      { ...meta, seq: next(), type: "citation", data: { id: "c1", title: "دليل الطالب", source: "kb" } },
      done(""),
    ]);
    expect(m.citations).toHaveLength(1);
  });

  it("records error events", () => {
    const m = run([token("جزء"), error("turn_failed")]);
    expect(m.status).toBe("error");
    expect(m.error).toEqual({ code: "turn_failed", message_ar: "رسالة الخادم", message_en: "server message", retryable: true });
  });

  it("ignores unknown event types", () => {
    const before = run([token("x")]);
    const after = applyEvent(before, { ...meta, seq: 99, type: "progress", data: {} } as unknown as StreamEvent);
    expect(after).toBe(before);
  });
});

describe("stream completion", () => {
  it("marks a stream that closed before done as interrupted", () => {
    const m = finalizeStream(run([token("نصف إجابة")]));
    expect(m.status).toBe("error");
    expect(m.error?.code).toBe("agent_stream_interrupted");
  });

  it("leaves finished messages untouched", () => {
    const finished = run([done("تم")]);
    expect(finalizeStream(finished)).toBe(finished);
    expect(markAborted(finished)).toBe(finished);
  });

  it("marks an in-flight message as aborted", () => {
    expect(markAborted(run([token("x")])).status).toBe("aborted");
  });
});

describe("resolveErrorMessage", () => {
  const translate = (key: string) => `t:${key}`;

  it("prefers the UI catalog for known codes", () => {
    expect(resolveErrorMessage({ code: "agent_unavailable", message_ar: "س", message_en: "s" }, "ar", translate)).toBe(
      "t:errors.agent_unavailable",
    );
  });

  it("falls back to the server message in the current locale", () => {
    const e = { code: "quota_exceeded", message_ar: "تجاوزت الحصة", message_en: "Quota exceeded" };
    expect(resolveErrorMessage(e, "ar", translate)).toBe("تجاوزت الحصة");
    expect(resolveErrorMessage(e, "en", translate)).toBe("Quota exceeded");
  });

  it("falls back to errors.unknown when nothing is usable", () => {
    expect(resolveErrorMessage({ code: "quota_exceeded", message_ar: " " }, "ar", translate)).toBe("t:errors.unknown");
  });
});
