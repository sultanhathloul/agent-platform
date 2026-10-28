import { describe, expect, it } from "vitest";
import type { Citation, KbDocumentView, KbToggle } from "@/lib/api/types";
import {
  EMPTY_METADATA,
  loadSelection,
  metadataProblems,
  saveSelection,
  selectedForTurn,
  supersedable,
  toMetadata,
  toggled,
} from "@/lib/kbs";
import { renderIn } from "@/test/render";
import { CitationList } from "./AssistantMessageView";
import { KbToggles } from "./KbToggles";
import { MetadataFields } from "./admin/KbDocumentsAdmin";

// Test-only slugs: no web file names a knowledge base.
const KBS: KbToggle[] = [
  { slug: "fixture-labor", name: { ar: "أنظمة العمل", en: "Labour rules" }, description: { ar: "وصف", en: "about" }, icon: "library" },
  { slug: "fixture-trade", name: { ar: "التجارة", en: "Trade" }, description: { ar: "", en: "" }, icon: "library" },
];

function text(html: string): string {
  return html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}

function doc(id: string, supersedes: string | null = null): KbDocumentView {
  return {
    id,
    kb: "fixture-labor",
    name: `${id}.pdf`,
    kind: "pdf",
    size: 1,
    status: "indexed",
    warnings: [],
    metadata: { title: `t-${id}`, publisher: "p", published_on: "2031-01-01", ...(supersedes ? { supersedes } : {}) },
    created_at: "2031-01-01T00:00:00Z",
  };
}

describe("the composer's knowledge base toggles (I3)", () => {
  it("lists only the KBs given, none on by default, each a pressed or unpressed button", () => {
    const html = renderIn("ar", <KbToggles kbs={KBS} selected={[]} onToggle={() => {}} />);
    expect([...html.matchAll(/data-kb="([^"]+)"/g)].map((m) => m[1])).toEqual(["fixture-labor", "fixture-trade"]);
    expect(html.match(/aria-pressed="false"/g)).toHaveLength(2);
    expect(text(html)).toContain("قواعد المعرفة");
    expect(text(html)).toContain("أنظمة العمل");
    const on = renderIn("en", <KbToggles kbs={KBS} selected={["fixture-trade"]} onToggle={() => {}} />);
    expect(on).toMatch(/aria-pressed="true"[^>]*data-kb="fixture-trade"/);
    expect(text(on)).toContain("Trade");
  });

  it("is not shown to a caller who may read no KB", () => {
    expect(renderIn("ar", <KbToggles kbs={[]} selected={["fixture-labor"]} onToggle={() => {}} />)).toBe("");
  });

  it("sends only toggled KBs still listed for the caller, and none when none is on", () => {
    expect(selectedForTurn([], KBS)).toEqual([]);
    expect(selectedForTurn(["fixture-trade", "gone", "fixture-labor"], KBS)).toEqual(["fixture-labor", "fixture-trade"]);
    expect(selectedForTurn(["fixture-labor"], [])).toEqual([]);
    expect(toggled(toggled([], "fixture-labor"), "fixture-labor")).toEqual([]);
  });

  it("keeps a conversation's toggles in this browser, per conversation", () => {
    const store = new Map<string, string>();
    const storage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v) };
    saveSelection("c1", ["fixture-labor"], storage);
    expect(loadSelection("c1", storage)).toEqual(["fixture-labor"]);
    expect(loadSelection("c2", storage)).toEqual([]);
    store.set("kb-selection:c3", "not json");
    expect(loadSelection("c3", storage)).toEqual([]);
    expect(loadSelection("c1", undefined)).toEqual([]);
  });
});

describe("a knowledge base citation (I4)", () => {
  const citation: Citation = {
    id: "d1",
    title: "آفاق الاقتصاد",
    source: "kb",
    kb: "fixture-labor",
    url: "https://example.org/r",
    document_id: "d1",
    pages: [3],
    kb_name: { ar: "الاقتصاد الدولي", en: "International economy" },
    publisher: "صندوق النقد",
    published_on: "2031-07-20",
  };

  it("shows KB › title, publisher, date in Western digits and page", () => {
    const ar = text(renderIn("ar", <CitationList citations={[citation]} />));
    expect(ar).toContain("الاقتصاد الدولي › آفاق الاقتصاد");
    expect(ar).toContain("صندوق النقد");
    expect(ar).toContain("بتاريخ 2031-07-20");
    const en = text(renderIn("en", <CitationList citations={[citation]} />));
    expect(en).toContain("International economy › آفاق الاقتصاد");
    expect(en).toContain("dated 2031-07-20");
    // The link opens the source; no slug is shown to the user.
    expect(renderIn("ar", <CitationList citations={[citation]} />)).toContain('href="https://example.org/r"');
    expect(ar).not.toContain("fixture-labor");
  });

  it("leaves a file citation as it was", () => {
    const file: Citation = { id: "a1", title: "لائحة.pdf", source: "doc", document_id: "a1", pages: [2] };
    const html = text(renderIn("ar", <CitationList citations={[file]} />));
    expect(html).toContain("لائحة.pdf");
    expect(html).not.toContain("›");
    expect(html).not.toContain("بتاريخ");
  });
});

describe("the admin's source metadata (D3, I5)", () => {
  it("needs a title, a publisher and a date; a link is optional but must be a link", () => {
    expect(metadataProblems(EMPTY_METADATA)).toEqual(["title", "publisher", "published_on"]);
    const ok = { ...EMPTY_METADATA, title: "تقرير", publisher: "هيئة", published_on: "2031-07-20" };
    expect(metadataProblems(ok)).toEqual([]);
    expect(metadataProblems({ ...ok, source_url: "example.org" })).toEqual(["source_url"]);
    expect(metadataProblems({ ...ok, published_on: "20/07/2031" })).toEqual(["published_on"]);
    // Arabic-Indic digits are taken as Western digits (ADR-0288).
    expect(metadataProblems({ ...ok, published_on: "٢٠٣١-٠٧-٢٠" })).toEqual([]);
    expect(toMetadata({ ...ok, published_on: "٢٠٣١-٠٧-٢٠", supersedes: "d0" })).toEqual({
      title: "تقرير",
      publisher: "هيئة",
      published_on: "2031-07-20",
      supersedes: "d0",
    });
  });

  it("offers as an earlier version only another document no one supersedes, and never a later version", () => {
    const docs = [doc("a"), doc("b", "a"), doc("c", "b"), doc("d")];
    expect(supersedable(docs, null).map((d) => d.id)).toEqual(["c", "d"]);
    expect(supersedable(docs, "a").map((d) => d.id)).toEqual(["d"]); // b and c are a's later versions
    expect(supersedable(docs, "b").map((d) => d.id)).toEqual(["a", "d"]); // b's own earlier version stays a choice
  });

  it("renders the form in Arabic and English, code-free, with the picker's choices", () => {
    const docs = [doc("a")];
    const ar = renderIn("ar", <MetadataFields draft={EMPTY_METADATA} onChange={() => {}} problems={[]} choices={docs} />);
    for (const label of ["عنوان الوثيقة", "الجهة الناشرة", "تاريخ النشر", "رابط المصدر (اختياري)", "تحل محل", "لا تحل محل وثيقة"]) {
      expect(text(ar)).toContain(label);
    }
    expect(ar).toContain('type="date"');
    expect(ar).toContain("t-a — p — 2031-01-01");
    const en = text(renderIn("en", <MetadataFields draft={EMPTY_METADATA} onChange={() => {}} problems={["title"]} choices={[]} />));
    expect(en).toContain("Publication date");
  });
});
