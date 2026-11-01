// Pure reducer from streamed agent events to the assistant message the UI renders.
import type { ArtifactRef, Citation, StreamEvent, Usage } from "@/lib/api/types";
import type { ErrorInfo } from "./errors";

export type Chip =
  | { kind: "tier"; key: string; role: string; step: string | null; tier: string; reason: string }
  | {
      kind: "guard";
      key: string;
      checkpoint: string;
      policyId: string | null;
      policyVersion: number | null;
      policyName: string | null;
      action: string;
      target: string | null;
    }
  | {
      kind: "tool";
      key: string;
      callId: string;
      tool: string;
      capability: string;
      status: string;
      latencyMs: number | null;
      // What the user reads while the tool runs, per locale (I13); null: a generic string.
      label: { ar: string; en: string } | null;
    };

export type AssistantStatus = "streaming" | "done" | "error" | "aborted";

/** The skill an answer is written with (phase 06): its slug and its name per locale. */
export type AppliedSkill = { slug: string; label: { ar: string; en: string } };

export type AssistantMessage = {
  text: string;
  status: AssistantStatus;
  receivedTokens: boolean;
  chips: Chip[];
  citations: Citation[];
  skill?: AppliedSkill;
  /** The report this turn wrote (reports PR): the chat's card reopens the panel at it. */
  artifact?: ArtifactRef;
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
  // No answer, so no skill chip either: nothing was written with it.
  return { ...message, skill: undefined, status: "error", text: "", receivedTokens: false, error };
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
          policyVersion: event.data.policy_version ?? null,
          policyName: event.data.policy_name ?? null,
          action: event.data.action,
          target: event.data.target ?? null,
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
          label: event.data.label ?? null,
        }),
      };
    case "tool_end": {
      // tool_end carries no label: the one tool_start brought stays.
      const started = m.chips.find((c) => c.key === `tool-${event.data.call_id}`);
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
          label: started && started.kind === "tool" ? started.label : null,
        }),
      };
    }
    case "citation":
      return { ...m, citations: mergeCitations(m.citations, [event.data]) };
    case "skill":
      // Sent before the answer's first token (ADR-0301): the reply's chip names the skill.
      return { ...m, skill: { slug: event.data.slug, label: { ar: event.data.label.ar, en: event.data.label.en } } };
    case "done":
      return {
        ...m,
        status: "done",
        // Tokens already shown stay; the final answer fills in only when nothing streamed.
        text: m.receivedTokens ? m.text : event.data.answer,
        citations: mergeCitations(m.citations, event.data.citations ?? []),
        usage: event.data.usage,
        traceId: event.data.trace_id ?? undefined,
        ...(event.data.artifact ? { artifact: event.data.artifact } : {}),
      };
    case "error":
      return failed(m, {
        code: event.data.code,
        message_ar: event.data.message_ar,
        message_en: event.data.message_en,
        retryable: event.data.retryable ?? false,
        ...(event.data.policy_id ? { policy_id: event.data.policy_id } : {}),
      });
    case "retract":
      // A check failed after the text streamed (ADR-0039): stop showing it; the error that follows takes its place.
      return { ...m, text: "", receivedTokens: false };
    default:
      // The artifact_* events drive the report panel, not the message (reports PR, I7).
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
