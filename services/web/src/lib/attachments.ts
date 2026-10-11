// A conversation's attachments through the gateway (phase 04, ADR-0129, ADR-0138). The browser
// never talks to MinIO: it uploads to the gateway, then polls each file's status every 2 s until
// it is indexed or failed. The composer keeps sending disabled while a file is uploading or pending.
import type { AttachmentLimits, AttachmentList, AttachmentView } from "@/lib/api/types";
import type { ErrorInfo } from "./errors";
import { GatewayError } from "./gateway";

export const POLL_INTERVAL_MS = 2000;

export type AttachmentStatus = "pending" | "indexed" | "failed";

/** A file in the composer: uploading (no `attachment` yet), registered, or refused. */
export type ComposerFile = {
  localId: string;
  name: string;
  size: number;
  progress: number; // 0–100 while uploading
  attachment?: AttachmentView;
  error?: ErrorInfo;
};

const MB = 1024 * 1024;

export function sizeInMb(bytes: number): number {
  return Math.round((bytes / MB) * 10) / 10;
}

export function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot < 0 ? "" : name.slice(dot + 1).toLowerCase();
}

/**
 * The refusal the gateway would give, checked before a byte leaves the browser (D2): type by
 * extension, size, and the number of files the conversation can still take. The gateway checks
 * again; this only spares a pointless upload.
 */
export function checkBeforeUpload(
  file: { name: string; size: number },
  limits: AttachmentLimits,
  filesInConversation: number,
): string | null {
  if (!limits.extensions.includes(extensionOf(file.name))) return "file_type_unsupported";
  if (file.size > limits.max_file_bytes) return "file_too_large";
  if (filesInConversation >= limits.max_files_per_conversation) return "attachment_limit";
  return null;
}

export function isTerminal(status: string | undefined): boolean {
  return status === "indexed" || status === "failed";
}

/** A file that still blocks sending: uploading, or registered and pending. */
export function isBusy(file: ComposerFile): boolean {
  if (file.error) return false;
  return !file.attachment || file.attachment.status === "pending";
}

/** The ids sent with the turn: every registered file of the composer that did not fail. */
export function turnAttachmentIds(files: ComposerFile[]): string[] {
  return files.filter((f) => f.attachment && f.attachment.status !== "failed").map((f) => f.attachment!.id);
}

/** The key of a failure reason's message under `attachments.reasons.*`. */
export function reasonKey(reason: string | null | undefined, known: (key: string) => boolean): string {
  const key = `attachments.reasons.${reason ?? "unknown"}`;
  return known(key) ? key : "attachments.reasons.unknown";
}

type Authorized = { gatewayUrl: string; token: string; signal?: AbortSignal };

function base(gatewayUrl: string, conversationId: string): string {
  return `${gatewayUrl}/v1/conversations/${encodeURIComponent(conversationId)}/attachments`;
}

async function readEnvelope<T>(res: Response): Promise<T> {
  const body = (await res.json()) as { data?: T; error?: ErrorInfo; request_id?: string };
  if (!res.ok || body.data === undefined) {
    throw new GatewayError(res.status, body.error ?? { code: "unknown" }, body.request_id);
  }
  return body.data;
}

async function call<T>(url: string, token: string, init: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, { ...init, headers: { Authorization: `Bearer ${token}`, Accept: "application/json" }, cache: "no-store" });
  } catch (error) {
    if ((error as { name?: string }).name === "AbortError") throw error;
    throw new GatewayError(0, { code: "network_error" });
  }
  if (res.status === 204) return undefined as T;
  return readEnvelope<T>(res);
}

export async function getLimits({ gatewayUrl, token, signal }: Authorized): Promise<AttachmentLimits> {
  return call<AttachmentLimits>(`${gatewayUrl}/v1/attachments/limits`, token, { method: "GET", signal });
}

export async function listAttachments({
  gatewayUrl,
  token,
  signal,
  conversationId,
}: Authorized & { conversationId: string }): Promise<AttachmentList> {
  return call<AttachmentList>(base(gatewayUrl, conversationId), token, { method: "GET", signal });
}

export async function getAttachment({
  gatewayUrl,
  token,
  signal,
  conversationId,
  id,
}: Authorized & { conversationId: string; id: string }): Promise<AttachmentView> {
  const url = `${base(gatewayUrl, conversationId)}/${encodeURIComponent(id)}`;
  return call<AttachmentView>(url, token, { method: "GET", signal });
}

export async function deleteAttachment({
  gatewayUrl,
  token,
  conversationId,
  id,
}: Authorized & { conversationId: string; id: string }): Promise<void> {
  const url = `${base(gatewayUrl, conversationId)}/${encodeURIComponent(id)}`;
  await call<void>(url, token, { method: "DELETE" });
}

/** The gateway URL that downloads the file (owner only); fetched with the bearer token. */
export function contentUrl(gatewayUrl: string, conversationId: string, id: string): string {
  return `${base(gatewayUrl, conversationId)}/${encodeURIComponent(id)}/content`;
}

/** Uploads one file with progress (XMLHttpRequest: fetch reports no upload progress). */
export function uploadAttachment({
  gatewayUrl,
  token,
  conversationId,
  file,
  onProgress,
}: {
  gatewayUrl: string;
  token: string;
  conversationId: string;
  file: File;
  onProgress: (percent: number) => void;
}): Promise<AttachmentView> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", base(gatewayUrl, conversationId));
    request.setRequestHeader("Authorization", `Bearer ${token}`);
    request.setRequestHeader("Accept", "application/json");
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.min(99, Math.round((event.loaded / event.total) * 100)));
    };
    request.onerror = () => reject(new GatewayError(0, { code: "network_error" }));
    request.onload = () => {
      let body: { data?: AttachmentView; error?: ErrorInfo; request_id?: string } = {};
      try {
        body = JSON.parse(request.responseText);
      } catch {
        // not an envelope: keep the status-derived failure below
      }
      if (request.status === 201 && body.data) {
        onProgress(100);
        resolve(body.data);
      } else {
        reject(new GatewayError(request.status, body.error ?? { code: request.status === 413 ? "file_too_large" : "unknown" }, body.request_id));
      }
    };
    const form = new FormData();
    form.append("file", file, file.name);
    request.send(form);
  });
}
