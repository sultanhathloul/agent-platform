// Gateway client. All API calls go to the gateway (README §13), never to the agent.
import type { ApiErrorEnvelope, ChatTurnRequest, StreamEvent } from "@/lib/api/types";
import type { ErrorInfo } from "./errors";
import { readSse, type SseMessage } from "./sse";

export class GatewayError extends Error {
  constructor(
    readonly status: number,
    readonly info: ErrorInfo,
    readonly requestId?: string,
  ) {
    super(info.code);
    this.name = "GatewayError";
  }
}

export function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && (error as { name?: unknown }).name === "AbortError";
}

function fallbackCode(status: number): string {
  if (status === 401) return "unauthorized";
  if (status === 403) return "forbidden";
  if (status === 400 || status === 422) return "invalid_request";
  if (status === 502 || status === 503 || status === 504) return "agent_unavailable";
  if (status >= 500) return "internal_error";
  return "unknown";
}

async function errorFromResponse(res: Response): Promise<GatewayError> {
  let info: ErrorInfo = { code: fallbackCode(res.status) };
  let requestId = res.headers.get("X-Request-Id") ?? undefined;
  try {
    const body = (await res.json()) as Partial<ApiErrorEnvelope>;
    if (body.error?.code) {
      info = { code: body.error.code, message_ar: body.error.message_ar, message_en: body.error.message_en };
    }
    requestId = body.request_id ?? requestId;
  } catch {
    // not a JSON envelope; keep the status-derived code
  }
  return new GatewayError(res.status, info, requestId);
}

export function parseEvent(message: SseMessage): StreamEvent | null {
  if (!message.data) return null;
  try {
    const value: unknown = JSON.parse(message.data);
    if (typeof value === "object" && value !== null) {
      const v = value as { type?: unknown; data?: unknown };
      if (typeof v.type === "string" && typeof v.data === "object" && v.data !== null) return value as StreamEvent;
    }
  } catch {
    // malformed event: skip it rather than break the whole answer
  }
  return null;
}

type StreamTurnArgs = {
  gatewayUrl: string;
  token: string;
  body: ChatTurnRequest;
  signal: AbortSignal;
};

/** Posts a turn and yields the agent's events as the gateway forwards them. */
export async function* streamTurn({ gatewayUrl, token, body, signal }: StreamTurnArgs): AsyncGenerator<StreamEvent> {
  let res: Response;
  try {
    res = await fetch(`${gatewayUrl}/v1/chat/turns`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify(body),
      signal,
      cache: "no-store",
    });
  } catch (error) {
    if (isAbortError(error)) throw error;
    throw new GatewayError(0, { code: "network_error" });
  }

  if (!res.ok) throw await errorFromResponse(res);
  if (!res.body) throw new GatewayError(res.status, { code: "agent_stream_interrupted" });

  try {
    for await (const message of readSse(res.body)) {
      const event = parseEvent(message);
      if (event) yield event;
    }
  } catch (error) {
    if (isAbortError(error)) throw error;
    throw new GatewayError(res.status, { code: "agent_stream_interrupted" });
  }
}
