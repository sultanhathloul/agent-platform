// The page «ملفاتي» / "My files" (my-files PR, owner decision 1, ADR-0442): pure, so it is tested without a
// browser. The filters as GET /v1/files takes them, a row's size and the link back to the conversation at the
// upload, with Western digits (ADR-0288).
import type { FileItem } from "@/lib/api/types";
import { PAGE_SIZE, dayStart } from "./library";

export const FILE_KINDS = ["pdf", "docx", "xlsx", "txt", "md"] as const;
export type FileKind = (typeof FILE_KINDS)[number];
export const FILE_STATUSES = ["indexed", "pending", "failed"] as const;
export type FileStatus = (typeof FILE_STATUSES)[number];

export type FileFilters = {
  /** The search words as typed; trimmed before they are sent. */
  q: string;
  /** "" for every type. */
  kind: FileKind | "";
  /** "" for every status. */
  status: FileStatus | "";
  /** The date inputs' values, YYYY-MM-DD, or "" when unset. */
  from: string;
  to: string;
};

export const EMPTY_FILE_FILTERS: FileFilters = { q: "", kind: "", status: "", from: "", to: "" };

/**
 * The query string for GET /v1/files. With search words the best matches come back and there are no pages, so
 * `before` is sent only without them; a "to" date includes the whole of that day.
 */
export function filesQuery(filters: FileFilters, before?: string | null, limit: number = PAGE_SIZE): string {
  const params = new URLSearchParams();
  const q = filters.q.trim();
  if (q) params.set("q", q);
  if (filters.kind) params.set("kind", filters.kind);
  if (filters.status) params.set("status", filters.status);
  const since = dayStart(filters.from);
  const until = dayStart(filters.to, true);
  if (since) params.set("since", since);
  if (until) params.set("until", until);
  if (before && !q) params.set("before", before);
  params.set("limit", String(Math.min(100, Math.max(1, Math.trunc(limit)))));
  return params.toString();
}

/** Whether any filter is set (the empty state then says nothing matched, not that there are none). */
export function filesFiltered(filters: FileFilters): boolean {
  return Boolean(filters.q.trim() || filters.kind || filters.status || filters.from || filters.to);
}

/** A file's size in the page's units: bytes, KB or MB, one decimal past a kilobyte, Western digits. */
export function fileSize(bytes: number, locale: string): string {
  const ar = locale === "ar";
  if (bytes < 1024) return ar ? `${bytes} بايت` : `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return ar ? `${kb.toFixed(1)} ك.ب` : `${kb.toFixed(1)} KB`;
  return ar ? `${(kb / 1024).toFixed(1)} م.ب` : `${(kb / 1024).toFixed(1)} MB`;
}

/** The conversation the file was uploaded in, opened at the message that carried it. */
export function uploadLink(item: Pick<FileItem, "conversation_id" | "id">): string {
  return `/chat/${encodeURIComponent(item.conversation_id)}?file=${encodeURIComponent(item.id)}`;
}

/** The next page after the shown files; a file already shown is not listed twice. */
export function appendFiles(shown: readonly FileItem[], page: readonly FileItem[]): FileItem[] {
  const seen = new Set(shown.map((item) => item.id));
  return [...shown, ...page.filter((item) => !seen.has(item.id))];
}

/** The list without a deleted file. */
export function withoutFile(shown: readonly FileItem[], id: string): FileItem[] {
  return shown.filter((item) => item.id !== id);
}
