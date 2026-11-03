// The report library page «تقاريري» (report library PR): pure, so it is tested without a browser.
// The filters as GET /v1/reports takes them, the date inputs as ISO 8601 times with the browser's
// offset, the source filter's options from the KBs GET /v1/capabilities lists, and what a row shows,
// with Western digits (ADR-0288).
import type { KbToggle, LibraryItem } from "@/lib/api/types";
import { labelIn } from "./kbs";

/** The search box waits this long after the last key before it asks again. */
export const SEARCH_DEBOUNCE_MS = 300;

/** One page of the newest reports (the API allows 1..100). */
export const PAGE_SIZE = 30;

/** The source filter's value for every source: the parameter is left out. */
export const ALL_SOURCES = "";

export type LibraryFilters = {
  /** The search words as typed; trimmed before they are sent. */
  q: string;
  /** The date inputs' values, YYYY-MM-DD, or "" when unset. */
  from: string;
  to: string;
  /** ALL_SOURCES, general, file or kb:<slug>. */
  source: string;
};

export const EMPTY_FILTERS: LibraryFilters = { q: "", from: "", to: "", source: ALL_SOURCES };

const DAY = /^(\d{4})-(\d{2})-(\d{2})$/;

const pad = (n: number, width = 2) => String(Math.abs(n)).padStart(width, "0");

/** A time as ISO 8601 with the browser's own offset at that time, e.g. 2026-11-02T00:00:00+03:00. */
export function isoWithOffset(at: Date): string {
  const offset = -at.getTimezoneOffset();
  const sign = offset >= 0 ? "+" : "-";
  const date = `${pad(at.getFullYear(), 4)}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}`;
  const time = `${pad(at.getHours())}:${pad(at.getMinutes())}:${pad(at.getSeconds())}`;
  return `${date}T${time}${sign}${pad(Math.trunc(offset / 60))}:${pad(offset % 60)}`;
}

/**
 * The start of a local day as ISO 8601; with `next`, the start of the day after, so a "to" date
 * includes the whole of that day (`until` is exclusive). Null for a value that is not a date.
 */
export function dayStart(day: string, next = false): string | null {
  const match = DAY.exec(day.trim());
  if (!match) return null;
  const [year, month, date] = [Number(match[1]), Number(match[2]), Number(match[3])];
  const at = new Date(year, month - 1, date + (next ? 1 : 0));
  // 2026-02-31 rolls over in Date: not a real day.
  const check = new Date(year, month - 1, date);
  if (check.getFullYear() !== year || check.getMonth() !== month - 1 || check.getDate() !== date) return null;
  return isoWithOffset(at);
}

/**
 * The query string for GET /v1/reports. With search words the best match comes first and there are
 * no pages, so `before` is sent only without them.
 */
export function libraryQuery(filters: LibraryFilters, before?: string | null, limit: number = PAGE_SIZE): string {
  const params = new URLSearchParams();
  const q = filters.q.trim();
  if (q) params.set("q", q);
  const since = dayStart(filters.from);
  const until = dayStart(filters.to, true);
  if (since) params.set("since", since);
  if (until) params.set("until", until);
  if (filters.source) params.set("source", filters.source);
  if (before && !q) params.set("before", before);
  params.set("limit", String(Math.min(100, Math.max(1, Math.trunc(limit)))));
  return params.toString();
}

/** Whether any filter is set (the empty state then says nothing matched, not that there are none). */
export function filtered(filters: LibraryFilters): boolean {
  return Boolean(filters.q.trim() || filters.from || filters.to || filters.source);
}

export type SourceOption = { value: string; label: string };

type FixedLabel = "library.sourceAll" | "library.sourceGeneral" | "library.sourceFile";

/** The source filter's options: all, general knowledge, attached files, then each KB the caller may read. */
export function sourceOptions(kbs: readonly KbToggle[], locale: string, translate: (key: FixedLabel) => string): SourceOption[] {
  return [
    { value: ALL_SOURCES, label: translate("library.sourceAll") },
    { value: "general", label: translate("library.sourceGeneral") },
    { value: "file", label: translate("library.sourceFile") },
    ...kbs.map((kb) => ({ value: `kb:${kb.slug}`, label: labelIn(kb.name, locale) || kb.slug })),
  ];
}

/** What a row names as its sources: general knowledge first when it used it, then the cited names. */
export function rowSources(item: Pick<LibraryItem, "sources" | "source_kinds">, generalLabel: string): string[] {
  const names = item.source_kinds.includes("general") ? [generalLabel, ...item.sources] : [...item.sources];
  return [...new Set(names.map((n) => n.trim()).filter(Boolean))];
}

/** The next page after the shown ones; a report already shown is not listed twice. */
export function appendPage(shown: readonly LibraryItem[], page: readonly LibraryItem[]): LibraryItem[] {
  const seen = new Set(shown.map((item) => item.id));
  return [...shown, ...page.filter((item) => !seen.has(item.id))];
}

// Gregorian dates in Western digits in both languages (ADR-0288), as the date inputs are.
const DATE_TAGS = { ar: "ar-u-nu-latn-ca-gregory", en: "en-GB" } as const;

/** A row's date: "2 نوفمبر 2026" in Arabic, "2 Nov 2026" in English. */
export function libraryDate(iso: string, locale: string, timeZone?: string): string {
  const ar = locale === "ar";
  return new Intl.DateTimeFormat(ar ? DATE_TAGS.ar : DATE_TAGS.en, {
    day: "numeric",
    month: ar ? "long" : "short",
    year: "numeric",
    ...(timeZone ? { timeZone } : {}),
  }).format(new Date(iso));
}
