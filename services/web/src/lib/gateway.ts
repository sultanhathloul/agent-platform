// Gateway client. All API calls go to the gateway (README §13), never to the agent.
import type {
  ApiErrorEnvelope,
  ChatTurnRequest,
  ConversationList,
  ConversationMessages,
  MeResponse,
  MemoryList,
  MemorySettings,
  PlatformSettingsView,
  PlatformUsage,
  PolicyChanges,
  PolicyDetail,
  PolicyDocument,
  PolicyImportReport,
  PolicyList,
  PolicyMetrics,
  PolicyOverrides,
  PolicyRoles,
  PolicyTestRequest,
  PolicyTestResult,
  PolicyView,
  PolicyYaml,
  SettingsDraft,
  SettingsPreview,
  SettingsSaved,
  SettingsVersions,
  SkillList,
  StreamEvent,
} from "@/lib/api/types";
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
  if (status === 409) return "turn_in_flight";
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
      info = {
        code: body.error.code,
        message_ar: body.error.message_ar,
        message_en: body.error.message_en,
        details: body.error.details ?? null,
      };
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

type Authorized = { gatewayUrl: string; token: string; signal?: AbortSignal };

/** Calls a JSON endpoint and returns the envelope's `data`; an error envelope becomes a GatewayError. */
async function callJson<T>(url: string, token: string, init: RequestInit): Promise<T | undefined> {
  let res: Response;
  try {
    res = await fetch(url, {
      ...init,
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/json",
        ...(init.body ? { "Content-Type": "application/json" } : {}),
      },
      cache: "no-store",
    });
  } catch (error) {
    if (isAbortError(error)) throw error;
    throw new GatewayError(0, { code: "network_error" });
  }
  if (!res.ok) throw await errorFromResponse(res);
  if (res.status === 204) return undefined;
  return ((await res.json()) as { data?: T }).data;
}

function required<T>(data: T | undefined, status: number): T {
  if (data === undefined) throw new GatewayError(status, { code: "unknown" });
  return data;
}

/** The caller's conversations, newest first; `before` is the previous page's `next_before` (ADR-0194). */
export async function listConversations({
  gatewayUrl,
  token,
  signal,
  before,
}: Authorized & { before?: string }): Promise<ConversationList> {
  const query = before ? `?before=${encodeURIComponent(before)}` : "";
  return required(await callJson<ConversationList>(`${gatewayUrl}/v1/conversations${query}`, token, { method: "GET", signal }), 200);
}

/** A conversation's messages, oldest first, as the model may see them again (ADR-0193). */
export async function getConversationMessages({
  gatewayUrl,
  token,
  signal,
  conversationId,
}: Authorized & { conversationId: string }): Promise<ConversationMessages> {
  const url = `${gatewayUrl}/v1/conversations/${encodeURIComponent(conversationId)}/messages`;
  return required(await callJson<ConversationMessages>(url, token, { method: "GET", signal }), 200);
}

/** The skills the caller may pick in the composer (phase 06, ADR-0292), labels from each SKILL.md. */
export async function listSkills({ gatewayUrl, token, signal }: Authorized): Promise<SkillList> {
  return required(await callJson<SkillList>(`${gatewayUrl}/v1/skills`, token, { method: "GET", signal }), 200);
}

/** The caller's memory items and memory switch (README §8). */
export async function getMemory({ gatewayUrl, token, signal }: Authorized): Promise<MemoryList> {
  return required(await callJson<MemoryList>(`${gatewayUrl}/v1/memory`, token, { method: "GET", signal }), 200);
}

export async function deleteMemoryItem({ gatewayUrl, token, signal, id }: Authorized & { id: string }): Promise<void> {
  await callJson(`${gatewayUrl}/v1/memory/${encodeURIComponent(id)}`, token, { method: "DELETE", signal });
}

export async function setMemoryEnabled({
  gatewayUrl,
  token,
  signal,
  enabled,
}: Authorized & { enabled: boolean }): Promise<MemorySettings> {
  const body = JSON.stringify({ enabled });
  const data = await callJson<MemorySettings>(`${gatewayUrl}/v1/memory/settings`, token, { method: "PUT", body, signal });
  return required(data, 200);
}

/** The caller as the gateway sees them; `is_admin` decides which links the UI shows, never access. */
export async function getMe({ gatewayUrl, token, signal }: Authorized): Promise<MeResponse> {
  return required(await callJson<MeResponse>(`${gatewayUrl}/v1/me`, token, { method: "GET", signal }), 200);
}

// ------------------------------------------------------------------ admin: guard policies (README §10.4)

const POLICIES = "/v1/admin/policies";

export async function listPolicies({ gatewayUrl, token, signal }: Authorized): Promise<PolicyList> {
  return required(await callJson<PolicyList>(`${gatewayUrl}${POLICIES}`, token, { method: "GET", signal }), 200);
}

export async function getPolicy({ gatewayUrl, token, signal, id }: Authorized & { id: string }): Promise<PolicyDetail> {
  const url = `${gatewayUrl}${POLICIES}/${encodeURIComponent(id)}`;
  return required(await callJson<PolicyDetail>(url, token, { method: "GET", signal }), 200);
}

export async function createPolicy({
  gatewayUrl,
  token,
  signal,
  policy,
}: Authorized & { policy: PolicyDocument }): Promise<PolicyView> {
  const body = JSON.stringify(policy);
  return required(await callJson<PolicyView>(`${gatewayUrl}${POLICIES}`, token, { method: "POST", body, signal }), 201);
}

export async function addPolicyVersion({
  gatewayUrl,
  token,
  signal,
  policy,
}: Authorized & { policy: PolicyDocument }): Promise<PolicyView> {
  const url = `${gatewayUrl}${POLICIES}/${encodeURIComponent(policy.id)}/versions`;
  return required(await callJson<PolicyView>(url, token, { method: "POST", body: JSON.stringify(policy), signal }), 201);
}

export async function setPolicyEnabled({
  gatewayUrl,
  token,
  signal,
  id,
  enabled,
}: Authorized & { id: string; enabled: boolean }): Promise<PolicyView> {
  const url = `${gatewayUrl}${POLICIES}/${encodeURIComponent(id)}/enabled`;
  const body = JSON.stringify({ enabled });
  return required(await callJson<PolicyView>(url, token, { method: "PUT", body, signal }), 200);
}

export async function testPolicy({
  gatewayUrl,
  token,
  signal,
  request,
}: Authorized & { request: PolicyTestRequest }): Promise<PolicyTestResult> {
  const body = JSON.stringify(request);
  const url = `${gatewayUrl}${POLICIES}/test`;
  return required(await callJson<PolicyTestResult>(url, token, { method: "POST", body, signal }), 200);
}

/** What a draft would override if saved now, and whether it is an exception (ADR-0105). */
export async function previewOverrides({
  gatewayUrl,
  token,
  signal,
  policy,
}: Authorized & { policy: PolicyDocument }): Promise<PolicyOverrides> {
  const url = `${gatewayUrl}${POLICIES}/overrides`;
  return required(await callJson<PolicyOverrides>(url, token, { method: "POST", body: JSON.stringify(policy), signal }), 200);
}

/** The policy's change log, newest first, with every change's actor (ADR-0108). */
export async function getPolicyChanges({ gatewayUrl, token, signal, id }: Authorized & { id: string }): Promise<PolicyChanges> {
  const url = `${gatewayUrl}${POLICIES}/${encodeURIComponent(id)}/changes`;
  return required(await callJson<PolicyChanges>(url, token, { method: "GET", signal }), 200);
}

/** The realm's policy:* roles, which role-scoped policies name (ADR-0107). */
export async function listPolicyRoles({ gatewayUrl, token, signal }: Authorized): Promise<PolicyRoles> {
  return required(await callJson<PolicyRoles>(`${gatewayUrl}/v1/admin/policy-roles`, token, { method: "GET", signal }), 200);
}

export async function exportPolicies({ gatewayUrl, token, signal }: Authorized): Promise<PolicyYaml> {
  return required(await callJson<PolicyYaml>(`${gatewayUrl}${POLICIES}/export`, token, { method: "GET", signal }), 200);
}

export async function importPolicies({
  gatewayUrl,
  token,
  signal,
  yaml,
}: Authorized & { yaml: string }): Promise<PolicyImportReport> {
  const body = JSON.stringify({ yaml });
  const url = `${gatewayUrl}${POLICIES}/import`;
  return required(await callJson<PolicyImportReport>(url, token, { method: "POST", body, signal }), 200);
}

export async function policyMetrics({
  gatewayUrl,
  token,
  signal,
  days,
}: Authorized & { days: number }): Promise<PolicyMetrics> {
  const url = `${gatewayUrl}${POLICIES}/metrics?days=${days}`;
  return required(await callJson<PolicyMetrics>(url, token, { method: "GET", signal }), 200);
}

const SETTINGS = "/v1/admin/settings";

/** The platform settings page (ADR-0276): every editable value, the read-only ones, the timeout tree. */
export async function getSettings({ gatewayUrl, token, signal }: Authorized): Promise<PlatformSettingsView> {
  return required(await callJson<PlatformSettingsView>(`${gatewayUrl}${SETTINGS}`, token, { method: "GET", signal }), 200);
}

/** What a draft would produce, and why it cannot be saved if it cannot; nothing is written. */
export async function previewSettings({
  gatewayUrl,
  token,
  signal,
  draft,
}: Authorized & { draft: SettingsDraft }): Promise<SettingsPreview> {
  const init = { method: "POST", body: JSON.stringify(draft), signal };
  return required(await callJson<SettingsPreview>(`${gatewayUrl}${SETTINGS}/preview`, token, init), 200);
}

/** Saves a draft as the next version; refused (settings_invalid) when a rule does not hold. */
export async function saveSettings({
  gatewayUrl,
  token,
  signal,
  draft,
}: Authorized & { draft: SettingsDraft }): Promise<SettingsSaved> {
  const init = { method: "POST", body: JSON.stringify(draft), signal };
  return required(await callJson<SettingsSaved>(`${gatewayUrl}${SETTINGS}`, token, init), 201);
}

/** Brings an earlier version's overrides back, as a new version. */
export async function revertSettings({
  gatewayUrl,
  token,
  signal,
  version,
  expectedVersion,
}: Authorized & { version: number; expectedVersion: number }): Promise<SettingsSaved> {
  const body = JSON.stringify({ version, expected_version: expectedVersion });
  return required(await callJson<SettingsSaved>(`${gatewayUrl}${SETTINGS}/revert`, token, { method: "POST", body, signal }), 201);
}

export async function listSettingsVersions({ gatewayUrl, token, signal }: Authorized): Promise<SettingsVersions> {
  const url = `${gatewayUrl}${SETTINGS}/versions?limit=50`;
  return required(await callJson<SettingsVersions>(url, token, { method: "GET", signal }), 200);
}

/** What is in use now: counts only (ADR-0282). */
/** The usage counts; `pageSession` is the page load's random id, so the gateway audits its first read (ADR-0287). */
export async function getUsage({
  gatewayUrl,
  token,
  signal,
  pageSession,
}: Authorized & { pageSession?: string }): Promise<PlatformUsage> {
  const query = pageSession ? `?page_session=${encodeURIComponent(pageSession)}` : "";
  return required(
    await callJson<PlatformUsage>(`${gatewayUrl}/v1/admin/usage${query}`, token, { method: "GET", signal }),
    200,
  );
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
