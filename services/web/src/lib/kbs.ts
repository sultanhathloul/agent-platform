// Knowledge bases in the web (phase 08): pure, so it is tested without a browser.
// The composer's toggles (I3): only the KBs GET /v1/capabilities lists, kept per conversation in
// this browser; none selected means the kb capability is not used. The admin forms (I5): D3 metadata
// checked before it is sent, with Western digits (ADR-0288).
import type { KbDocumentView, KbSourceMetadata, KbToggle } from "@/lib/api/types";

const STORAGE_PREFIX = "kb-selection:";

/** The selected slugs that are still among the KBs the caller may read, in their listed order. */
export function selectedForTurn(selected: readonly string[], available: readonly KbToggle[]): string[] {
  return available.filter((kb) => selected.includes(kb.slug)).map((kb) => kb.slug);
}

/** Toggles one slug on or off. */
export function toggled(selected: readonly string[], slug: string): string[] {
  return selected.includes(slug) ? selected.filter((s) => s !== slug) : [...selected, slug];
}

type Store = Pick<Storage, "getItem" | "setItem">;

/** A conversation's toggles as this browser last kept them (README §13: persisted per conversation). */
export function loadSelection(conversationId: string, store: Store | undefined): string[] {
  try {
    const raw = store?.getItem(STORAGE_PREFIX + conversationId);
    const value: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(value) ? value.filter((s): s is string => typeof s === "string") : [];
  } catch {
    return [];
  }
}

export function saveSelection(conversationId: string, selected: readonly string[], store: Store | undefined): void {
  try {
    store?.setItem(STORAGE_PREFIX + conversationId, JSON.stringify(selected));
  } catch {
    // a browser without storage keeps the toggles for this page only
  }
}

/** A label in the page's language. */
export function labelIn(label: { ar: string; en: string } | null | undefined, locale: string): string {
  if (!label) return "";
  return locale === "ar" ? label.ar : label.en;
}

/** A slug as an admin may type it: lower-case letters, digits, - and _, starting with a letter. */
export const SLUG_PATTERN = /^[a-z][a-z0-9_-]{0,62}$/;

export type MetadataDraft = {
  title: string;
  publisher: string;
  source_url: string;
  published_on: string;
  supersedes: string;
};

export type MetadataProblem = "title" | "publisher" | "source_url" | "published_on";

export const EMPTY_METADATA: MetadataDraft = { title: "", publisher: "", source_url: "", published_on: "", supersedes: "" };

/** Arabic-Indic and Persian digits typed into a field, as Western digits (ADR-0288). */
export function westernDigits(text: string): string {
  return text.replace(/[٠-٩۰-۹]/g, (d) => String((d.charCodeAt(0) - 0x0660) % 0x90));
}

/** What is wrong with a D3 draft, field by field; empty when it may be sent. */
export function metadataProblems(draft: MetadataDraft): MetadataProblem[] {
  const problems: MetadataProblem[] = [];
  if (!draft.title.trim() || draft.title.trim().length > 300) problems.push("title");
  if (!draft.publisher.trim() || draft.publisher.trim().length > 200) problems.push("publisher");
  const url = draft.source_url.trim();
  if (url && (!/^https?:\/\/\S+$/.test(url) || url.length > 2000)) problems.push("source_url");
  const date = westernDigits(draft.published_on.trim());
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || Number.isNaN(Date.parse(`${date}T00:00:00Z`))) problems.push("published_on");
  return problems;
}

/** The draft as the API takes it. */
export function toMetadata(draft: MetadataDraft): KbSourceMetadata {
  const url = draft.source_url.trim();
  return {
    title: draft.title.trim(),
    publisher: draft.publisher.trim(),
    published_on: westernDigits(draft.published_on.trim()),
    ...(url ? { source_url: url } : {}),
    ...(draft.supersedes ? { supersedes: draft.supersedes } : {}),
  };
}

export function draftOf(document: KbDocumentView): MetadataDraft {
  const m = document.metadata;
  return {
    title: m.title,
    publisher: m.publisher,
    source_url: m.source_url ?? "",
    published_on: m.published_on,
    supersedes: m.supersedes ?? "",
  };
}

/**
 * The documents a document may supersede: another document of the same KB that no other document
 * supersedes already, and that is not a later version of this one (no loop). The server checks too.
 */
export function supersedable(documents: readonly KbDocumentView[], self: string | null): KbDocumentView[] {
  const earlier = new Map(documents.map((d) => [d.id, d.metadata.supersedes ?? null]));
  const taken = new Set(documents.filter((d) => d.id !== self && d.metadata.supersedes).map((d) => d.metadata.supersedes));
  const laterThanSelf = (id: string): boolean => {
    // `id` is later than self when following its earlier versions reaches self.
    const seen = new Set<string>();
    let at: string | null | undefined = earlier.get(id);
    while (at && !seen.has(at)) {
      if (at === self) return true;
      seen.add(at);
      at = earlier.get(at);
    }
    return false;
  };
  return documents.filter((d) => d.id !== self && !taken.has(d.id) && !(self && laterThanSelf(d.id)));
}

/** A KB's realm role, as the admin sees it before the KB exists (ADR-0006). */
export function roleOf(slug: string): string {
  return `kb:${slug}:read`;
}

/** A document as the supersedes picker lists it: title, publisher and date. */
export function versionLabel(document: KbDocumentView): string {
  const m = document.metadata;
  return `${m.title} — ${m.publisher} — ${m.published_on}`;
}
