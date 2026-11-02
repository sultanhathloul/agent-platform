import { describe, expect, it } from "vitest";
import type { LibraryItem } from "@/lib/api/types";
import { EMPTY_FILTERS, sourceOptions } from "@/lib/library";
import { CATALOGS, renderIn, type TestLocale } from "@/test/render";
import { ReportLibraryView, type ReportLibraryViewProps } from "./ReportLibrary";

const KBS = [{ slug: "stats", name: { ar: "الإحصاءات", en: "Statistics" }, description: { ar: "", en: "" }, icon: "book" }];

const ROWS: LibraryItem[] = [
  {
    id: "r1",
    conversation_id: "0b7c1e2a-1111-4222-8333-944455556666",
    title: "لوحة التضخم",
    latest_version: 3,
    matched_version: 1,
    subject: "التضخم في المملكة 2025",
    sections: ["الملخص", "الأسعار"],
    sources: ["الهيئة العامة للإحصاء"],
    source_kinds: ["general", "kb:stats"],
    created_at: "2026-10-01T09:00:00Z",
    updated_at: "2026-11-02T09:00:00Z",
  },
  {
    id: "r2",
    conversation_id: "1c8d2f3b-2222-4333-8444-a55566667777",
    title: "Budget 2026",
    latest_version: 1,
    matched_version: 1,
    subject: "",
    sections: [],
    sources: ["budget.pdf"],
    source_kinds: ["file"],
    created_at: "2026-09-01T09:00:00Z",
    updated_at: "2026-09-01T09:00:00Z",
  },
];

function props(locale: TestLocale, extra: Partial<ReportLibraryViewProps> = {}): ReportLibraryViewProps {
  const noop = () => {};
  const catalog = CATALOGS[locale].library as Record<string, string>;
  return {
    filters: EMPTY_FILTERS,
    applied: EMPTY_FILTERS,
    options: sourceOptions(KBS, locale, (key) => catalog[key.replace("library.", "")] ?? key),
    items: ROWS,
    status: "ready",
    error: null,
    hasMore: false,
    loadingMore: false,
    downloading: null,
    openId: null,
    onFilters: noop,
    onClear: noop,
    onRetry: noop,
    onMore: noop,
    onOpen: noop,
    onDownload: noop,
    ...extra,
  };
}

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();

describe("the report library «تقاريري»", () => {
  it("renders each row: title, version, date, sources, subject and the conversation link, in Arabic", () => {
    const html = renderIn("ar", <ReportLibraryView {...props("ar")} />);
    const shown = text(html);
    expect(shown).toContain("تقاريري");
    expect(shown).toContain("لوحة التضخم");
    expect(shown).toContain("الإصدار 3");
    expect(shown).toContain("آخر تحديث");
    expect(shown).toContain("2026");
    expect(shown).toContain("المعرفة العامة");
    expect(shown).toContain("الهيئة العامة للإحصاء");
    expect(shown).toContain("التضخم في المملكة 2025");
    expect(shown).toContain("فتح المحادثة");
    expect(html).toContain('href="/chat/0b7c1e2a-1111-4222-8333-944455556666"');
    expect(html).toContain('aria-label="تنزيل آخر إصدار من «لوحة التضخم»"');
    // Western digits only (ADR-0288).
    expect(/[٠-٩]/.test(shown)).toBe(false);
    // The matched version is told only for a search.
    expect(shown).not.toContain("أقرب تطابق");
  });

  it("has no English UI text in Arabic beyond the reports' own data", () => {
    const shown = text(renderIn("ar", <ReportLibraryView {...props("ar", { items: ROWS.slice(0, 1) })} />));
    expect(/[A-Za-z]/.test(shown)).toBe(false);
  });

  it("offers the filters: search, the two dates and the sources with each KB as kb:<slug>", () => {
    const html = renderIn("ar", <ReportLibraryView {...props("ar")} />);
    expect(html).toContain('type="search"');
    expect(html.match(/type="date"/g)).toHaveLength(2);
    const options = [...html.matchAll(/<option[^>]*value="([^"]*)"[^>]*>([^<]*)<\/option>/g)].map((m) => [m[1], m[2]]);
    expect(options).toEqual([
      ["", "كل المصادر"],
      ["general", "المعرفة العامة"],
      ["file", "ملفات مرفقة"],
      ["kb:stats", "الإحصاءات"],
    ]);
    expect(text(html)).toContain("من تاريخ");
    expect(text(html)).toContain("إلى تاريخ");
  });

  it("bounds the date inputs by each other and shows the chosen source", () => {
    const filters = { q: "", from: "2026-10-01", to: "2026-10-31", source: "file" };
    const html = renderIn("ar", <ReportLibraryView {...props("ar", { filters, applied: filters })} />);
    expect(html).toContain('max="2026-10-31"');
    expect(html).toContain('min="2026-10-01"');
    expect(html).toContain('<option value="file" selected="">');
  });

  it("tells the best-matching version when searched", () => {
    const filters = { ...EMPTY_FILTERS, q: "التضخم" };
    expect(text(renderIn("ar", <ReportLibraryView {...props("ar", { filters, applied: filters })} />))).toContain("أقرب تطابق في الإصدار 1");
  });

  it("shows the empty state, and a different one when filtered", () => {
    expect(text(renderIn("ar", <ReportLibraryView {...props("ar", { items: [] })} />))).toContain("لا توجد تقارير بعد");
    const filters = { ...EMPTY_FILTERS, source: "general" };
    const none = text(renderIn("ar", <ReportLibraryView {...props("ar", { items: [], filters, applied: filters })} />));
    expect(none).toContain("لا توجد تقارير تطابق البحث أو التصفية.");
    expect(text(renderIn("en", <ReportLibraryView {...props("en", { items: [] })} />))).toContain("No reports yet.");
  });

  it("shows the loading state, and the error with a retry", () => {
    expect(text(renderIn("ar", <ReportLibraryView {...props("ar", { status: "loading", items: [] })} />))).toContain("جارٍ تحميل التقارير…");
    const failed = renderIn(
      "ar",
      <ReportLibraryView {...props("ar", { status: "failed", items: [], error: { code: "invalid_request" } })} />,
    );
    expect(failed).toContain('role="alert"');
    expect(text(failed)).toContain(CATALOGS.ar.errors.invalid_request);
    expect(text(failed)).toContain("إعادة المحاولة");
  });

  it("offers load more only when there is a next page", () => {
    expect(text(renderIn("ar", <ReportLibraryView {...props("ar")} />))).not.toContain("عرض المزيد");
    expect(text(renderIn("ar", <ReportLibraryView {...props("ar", { hasMore: true })} />))).toContain("عرض المزيد");
    expect(text(renderIn("en", <ReportLibraryView {...props("en", { hasMore: true })} />))).toContain("Load more");
  });

  it("marks the report open in the panel", () => {
    const html = renderIn("ar", <ReportLibraryView {...props("ar", { openId: "r2" })} />);
    expect(html).toMatch(/data-library-report="r2" class="[^"]*ring-focus/);
    expect(html).not.toMatch(/data-library-report="r1" class="[^"]*ring-focus/);
  });

  it("renders in English with the same structure", () => {
    const shown = text(renderIn("en", <ReportLibraryView {...props("en")} />));
    expect(shown).toContain("My reports");
    expect(shown).toContain("Version 3");
    expect(shown).toContain("General knowledge");
    expect(shown).toContain("Open conversation");
    expect(shown).toContain("2 Nov 2026");
  });
});

describe("the header", () => {
  it("has the catalog entries for the «تقاريري» link", () => {
    expect(CATALOGS.ar.header.reports).toBe("تقاريري");
    expect(CATALOGS.en.header.reports).toBe("My reports");
  });
});
