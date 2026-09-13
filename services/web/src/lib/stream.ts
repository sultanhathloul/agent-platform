// Pure reducer from streamed agent events to the assistant message the UI renders.
import type { Citation, StreamEvent, Usage } from "@/lib/api/types";
import type { ErrorInfo } from "./errors";

export type Chip =
  | { kind: "tier"; key: string; role: string; step: string | null; tier: string; reason: string }
  | { kind: "guard"; key: string; checkpoint: string; policyId: string | null; action: string; detail: string | null }
  | { kind: "tool"; key: string; callId: string; tool: string; capability: string; status: string; latencyMs: number | null };

export type AssistantStatus = "streaming" | "done" | "error" | "aborted";

export type AssistantMessage = {
  text: string;
  status: AssistantStatus;
  receivedTokens: boolean;
  chips: Chip[];
  citations: Citation[];
  usage?: Usage;
  traceId?: string;
  requestId?: string;
  error?: ErrorInfo;
};

export function initialAssistant(): AssistantMessage {
  return { text: "", status: "streaming", receivedTokens: false, chips: [], citations: [] };
}

function upsertChip(chips: Chip[], chip: Chip): Chip[] {
  const index = chips.findIndex((c) => c.key === chip.key);
  if (index === -1) return [...chips, chip];
  const next = chips.slice();
  next[index] = chip;
  return next;
}

function mergeCitations(current: Citation[], incoming: Citation[]): Citation[] {
  const seen = new Set(current.map((c) => c.id));
  return [...current, ...incoming.filter((c) => !seen.has(c.id))];
}

// A turn that ends in an error has no answer: text streamed before it is withdrawn, not left
// on screen beside the error (ADR-0048).
function failed(message: AssistantMessage, error: ErrorInfo): AssistantMessage {
  return { ...message, status: "error", text: "", receivedTokens: false, error };
}

export function applyEvent(message: AssistantMessage, event: StreamEvent): AssistantMessage {
  const m = message.requestId ? message : { ...message, requestId: event.request_id };
  switch (event.type) {
    case "token":
      return { ...m, text: m.text + event.data.text, receivedTokens: true };
    case "tier":
      return {
        ...m,
        chips: upsertChip(m.chips, {
          kind: "tier",
          key: `tier-${event.seq}`,
          role: event.data.role,
          step: event.data.step ?? null,
          tier: event.data.tier,
          reason: event.data.reason,
        }),
      };
    case "guard":
      return {
        ...m,
        chips: upsertChip(m.chips, {
          kind: "guard",
          key: `guard-${event.seq}`,
          checkpoint: event.data.checkpoint,
          policyId: event.data.policy_id ?? null,
          action: event.data.action,
          detail: event.data.detail ?? null,
        }),
      };
    case "tool_start":
      return {
        ...m,
        chips: upsertChip(m.chips, {
          kind: "tool",
          key: `tool-${event.data.call_id}`,
          callId: event.data.call_id,
          tool: event.data.tool,
          capability: event.data.capability,
          status: "running",
          latencyMs: null,
        }),
      };
    case "tool_end":
      return {
        ...m,
        chips: upsertChip(m.chips, {
          kind: "tool",
          key: `tool-${event.data.call_id}`,
          callId: event.data.call_id,
          tool: event.data.tool,
          capability: event.data.capability,
          status: event.data.status,
          latencyMs: event.data.latency_ms,
        }),
      };
    case "citation":
      return { ...m, citations: mergeCitations(m.citations, [event.data]) };
    case "done":
      return {
        ...m,
        status: "done",
        // Tokens already shown stay; the final answer fills in only when nothing streamed.
        text: m.receivedTokens ? m.text : event.data.answer,
        citations: mergeCitations(m.citations, event.data.citations ?? []),
        usage: event.data.usage,
        traceId: event.data.trace_id ?? undefined,
      };
    case "error":
      return failed(m, {
        code: event.data.code,
        message_ar: event.data.message_ar,
        message_en: event.data.message_en,
        retryable: event.data.retryable ?? false,
      });
    case "retract":
      // A check failed after the text streamed (ADR-0039): stop showing it; the error that follows takes its place.
      return { ...m, text: "", receivedTokens: false };
    default:
      return message; // event types from a newer server (or `progress`, not rendered yet) are ignored
  }
}

/** Called when the stream closes: a turn that never reached done/error was interrupted. */
export function finalizeStream(message: AssistantMessage): AssistantMessage {
  if (message.status !== "streaming") return message;
  return failed(message, { code: "agent_stream_interrupted", retryable: true });
}

export function markAborted(message: AssistantMessage): AssistantMessage {
  return message.status === "streaming" ? { ...message, status: "aborted" } : message;
}

export function markError(message: AssistantMessage, error: ErrorInfo): AssistantMessage {
  return failed(message, error);
}
