import { describe, expect, it } from "vitest";
import type { FileItem } from "@/lib/api/types";
import { EMPTY_FILE_FILTERS } from "@/lib/files";
import { renderIn, type TestLocale } from "@/test/render";
import { MyFilesView, type MyFilesViewProps } from "./MyFiles";

const ROWS: FileItem[] = [
  {
    id: "f1",
    conversation_id: "0b7c1e2a-1111-4222-8333-944455556666",
    conversation_title: "هذا تحليل الدم الخاص بي",
    conversation_created_at: "2026-11-01T08:00:00Z",
    name: "تحليل-الدم.pdf",
    mime: "application/pdf",
    kind: "pdf",
    size: 2048,
    status: "indexed",
    pages: 2,
    created_at: "2026-11-01T08:01:00Z",
  },
  {
    id: "f2",
    conversation_id: "1c8d2f3b-2222-4333-8444-a55566667777",
    conversation_title: "",
    name: "scan.pdf",
    mime: "application/pdf",
    kind: "pdf",
    size: 900,
    status: "failed",
    reason: "scanned_unsupported",
    created_at: "2026-10-20T08:01:00Z",
  },
];

function props(extra: Partial<MyFilesViewProps> = {}): MyFilesViewProps {
  const noop = () => {};
  return {
    filters: EMPTY_FILE_FILTERS,
    applied: EMPTY_FILE_FILTERS,
    items: ROWS,
    status: "ready",
    error: null,
    hasMore: false,
    loadingMore: false,
    busy: null,
    confirming: null,
    onFilters: noop,
    onClear: noop,
    onRetry: noop,
    onMore: noop,
    onDownload: noop,
    onDelete: noop,
    onConfirm: noop,
    onCancel: noop,
    ...extra,
  };
}

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
const render = (locale: TestLocale, extra: Partial<MyFilesViewProps> = {}) => renderIn(locale, <MyFilesView {...props(extra)} />);

describe("the page «ملفاتي»", () => {
  it("renders each file: name, type, size, status, conversation and date, and the link back to the upload", () => {
    const html = render("ar");
    const shown = text(html);
    expect(shown).toContain("ملفاتي");
    expect(shown).toContain("تحليل-الدم.pdf");
    expect(shown).toContain("2.0 ك.ب");
    expect(shown).toContain("جاهز للبحث");
    expect(shown).toContain("هذا تحليل الدم الخاص بي");
    expect(shown).toContain("رُفع في");
    expect(shown).toContain("محادثة بلا عنوان");
    expect(shown).toContain("تعذّرت معالجته");
    expect(html).toContain('href="/chat/0b7c1e2a-1111-4222-8333-944455556666?file=f1"');
    expect(html).toContain('aria-label="تنزيل «تحليل-الدم.pdf»"');
    expect(html).toContain('aria-label="حذف «تحليل-الدم.pdf»"');
    // Western digits only (ADR-0288).
    expect(/[٠-٩]/.test(shown)).toBe(false);
  });

  it("has no English UI text in Arabic beyond the files' own names and types", () => {
    const shown = text(render("ar", { items: ROWS.slice(0, 1) }))
      .replace(/\b(pdf|docx|xlsx|txt|md|PDF|DOCX|XLSX|TXT|MD)\b/g, "")
      .replace("تحليل-الدم.", "");
    expect(/[A-Za-z]/.test(shown)).toBe(false);
  });

  it("offers search, type, status and the two dates", () => {
    const html = render("ar");
    expect(html).toContain('type="search"');
    expect(html.match(/type="date"/g)).toHaveLength(2);
    const options = [...html.matchAll(/<option[^>]*value="([^"]*)"[^>]*>([^<]*)<\/option>/g)].map((m) => m[1]);
    expect(options).toEqual(["", "pdf", "docx", "xlsx", "txt", "md", "", "indexed", "pending", "failed"]);
  });

  it("asks for confirmation before a delete and says the conversation stays", () => {
    const shown = text(render("ar", { confirming: "f1" }));
    expect(shown).toContain("سيُحذف «تحليل-الدم.pdf» من كل المحادثات ومن البحث");
    expect(shown).toContain("تبقى المحادثة نفسها");
    expect(shown).toContain("تأكيد الحذف");
    expect(shown).toContain("إلغاء");
    const english = text(render("en", { confirming: "f1" }));
    expect(english).toContain("will be deleted from every conversation and from search");
  });

  it("shows the empty state, a different one when filtered, and the loading state", () => {
    expect(text(render("ar", { items: [] }))).toContain("لا توجد ملفات بعد");
    const filters = { ...EMPTY_FILE_FILTERS, kind: "docx" as const };
    expect(text(render("ar", { items: [], filters, applied: filters }))).toContain("لا توجد ملفات تطابق البحث أو التصفية.");
    expect(text(render("en", { items: [] }))).toContain("No files yet.");
    expect(text(render("ar", { items: [], status: "loading" }))).toContain("جارٍ تحميل الملفات…");
  });

  it("says in the page that earlier files are used only when the question refers to them", () => {
    expect(text(render("ar"))).toContain("إلا إذا أشرت إليها في سؤالك");
    expect(text(render("en"))).toContain("only when your question refers to them");
  });
});
