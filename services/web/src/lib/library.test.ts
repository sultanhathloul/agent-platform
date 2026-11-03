import { afterEach, describe, expect, it } from "vitest";
import type { KbToggle, LibraryItem } from "@/lib/api/types";
import {
  ALL_SOURCES,
  EMPTY_FILTERS,
  appendPage,
  dayStart,
  filtered,
  isoWithOffset,
  libraryDate,
  libraryQuery,
  rowSources,
  sourceOptions,
} from "./library";

const KBS: KbToggle[] = [
  { slug: "stats", name: { ar: "الإحصاءات", en: "Statistics" }, description: { ar: "", en: "" }, icon: "book" },
  { slug: "law", name: { ar: "الأنظمة", en: "Regulations" }, description: { ar: "", en: "" }, icon: "book" },
];

function item(id: string, extra: Partial<LibraryItem> = {}): LibraryItem {
  return {
    id,
    conversation_id: "c1",
    title: "لوحة التضخم",
    latest_version: 2,
    matched_version: 2,
    subject: "التضخم في المملكة 2025",
    sections: ["الملخص"],
    sources: [],
    source_kinds: [],
    created_at: "2026-10-01T09:00:00Z",
    updated_at: "2026-11-02T09:00:00Z",
    ...extra,
  };
}

const originalTz = process.env.TZ;
afterEach(() => {
  process.env.TZ = originalTz;
});

describe("the date filter as ISO 8601 times", () => {
  it("is the start of the local day with its offset, and the next day's start for 'to'", () => {
    process.env.TZ = "Asia/Riyadh";
    expect(dayStart("2026-11-02")).toBe("2026-11-02T00:00:00+03:00");
    expect(dayStart("2026-11-02", true)).toBe("2026-11-03T00:00:00+03:00");
    expect(dayStart("2026-12-31", true)).toBe("2027-01-01T00:00:00+03:00");
  });

  it("writes a negative offset and minutes too", () => {
    process.env.TZ = "America/St_Johns";
    expect(isoWithOffset(new Date(2026, 0, 15, 8, 5, 9))).toBe("2026-01-15T08:05:09-03:30");
    process.env.TZ = "UTC";
    expect(dayStart("2026-03-01")).toBe("2026-03-01T00:00:00+00:00");
  });

  it("is the same instant as the local midnight", () => {
    const iso = dayStart("2026-06-10");
    expect(iso).not.toBeNull();
    expect(Date.parse(iso as string)).toBe(new Date(2026, 5, 10).getTime());
  });

  it("drops what is not a real date", () => {
    expect(dayStart("")).toBeNull();
    expect(dayStart("2026-02-31")).toBeNull();
    expect(dayStart("02/11/2026")).toBeNull();
    expect(dayStart("٢٠٢٦-١١-٠٢")).toBeNull();
  });
});

describe("the query string for GET /v1/reports", () => {
  it("sends only the limit with no filter", () => {
    expect(libraryQuery(EMPTY_FILTERS)).toBe("limit=30");
  });

  it("sends the trimmed Arabic search words, encoded, and no page cursor with them", () => {
    const query = new URLSearchParams(libraryQuery({ ...EMPTY_FILTERS, q: "  التضخم  " }, "2026-10-01T00:00:00Z"));
    expect(query.get("q")).toBe("التضخم");
    expect(query.has("before")).toBe(false);
  });

  it("pages with before when there are no search words", () => {
    const query = new URLSearchParams(libraryQuery({ ...EMPTY_FILTERS, q: "   " }, "2026-10-01T00:00:00Z"));
    expect(query.has("q")).toBe(false);
    expect(query.get("before")).toBe("2026-10-01T00:00:00Z");
  });

  it("sends the dates, with 'to' inclusive of its day, and the source", () => {
    process.env.TZ = "Asia/Riyadh";
    const query = new URLSearchParams(libraryQuery({ q: "", from: "2026-10-01", to: "2026-10-31", source: "kb:stats" }));
    expect(query.get("since")).toBe("2026-10-01T00:00:00+03:00");
    expect(query.get("until")).toBe("2026-11-01T00:00:00+03:00");
    expect(query.get("source")).toBe("kb:stats");
    // The + of the offset is encoded, not read as a space.
    expect(libraryQuery({ ...EMPTY_FILTERS, from: "2026-10-01" })).toContain("since=2026-10-01T00%3A00%3A00%2B03%3A00");
  });

  it("leaves out the source for all sources and a malformed date, and keeps the limit in 1..100", () => {
    const query = new URLSearchParams(libraryQuery({ q: "", from: "x", to: "", source: ALL_SOURCES }, null, 500));
    expect([...query.keys()]).toEqual(["limit"]);
    expect(query.get("limit")).toBe("100");
    expect(new URLSearchParams(libraryQuery(EMPTY_FILTERS, null, 0)).get("limit")).toBe("1");
  });

  it("knows when a filter is set", () => {
    expect(filtered(EMPTY_FILTERS)).toBe(false);
    expect(filtered({ ...EMPTY_FILTERS, q: "  " })).toBe(false);
    expect(filtered({ ...EMPTY_FILTERS, q: "تقرير" })).toBe(true);
    expect(filtered({ ...EMPTY_FILTERS, source: "file" })).toBe(true);
    expect(filtered({ ...EMPTY_FILTERS, to: "2026-11-02" })).toBe(true);
  });
});

describe("the source filter's options", () => {
  const labels = { "library.sourceAll": "كل المصادر", "library.sourceGeneral": "المعرفة العامة", "library.sourceFile": "ملفات مرفقة" };

  it("lists all, general knowledge, attached files, then each KB as kb:<slug> in the page's language", () => {
    expect(sourceOptions(KBS, "ar", (key) => labels[key])).toEqual([
      { value: "", label: "كل المصادر" },
      { value: "general", label: "المعرفة العامة" },
      { value: "file", label: "ملفات مرفقة" },
      { value: "kb:stats", label: "الإحصاءات" },
      { value: "kb:law", label: "الأنظمة" },
    ]);
    expect(sourceOptions(KBS, "en", (key) => key).slice(3).map((o) => o.label)).toEqual(["Statistics", "Regulations"]);
  });

  it("offers the fixed three without any KB", () => {
    expect(sourceOptions([], "ar", (key) => labels[key]).map((o) => o.value)).toEqual(["", "general", "file"]);
  });
});

describe("a row", () => {
  it("names general knowledge first when the report used it, then the cited sources once each", () => {
    const row = item("a", { source_kinds: ["general", "kb:stats"], sources: ["الهيئة العامة للإحصاء", "الهيئة العامة للإحصاء", " "] });
    expect(rowSources(row, "المعرفة العامة")).toEqual(["المعرفة العامة", "الهيئة العامة للإحصاء"]);
    expect(rowSources(item("b", { source_kinds: ["file"], sources: ["report.pdf"] }), "المعرفة العامة")).toEqual(["report.pdf"]);
    expect(rowSources(item("c"), "المعرفة العامة")).toEqual([]);
  });

  it("shows its date in Western digits in both languages, Gregorian", () => {
    const ar = libraryDate("2026-11-02T09:00:00Z", "ar", "UTC");
    expect(ar).toBe("2 نوفمبر 2026");
    expect(/[٠-٩]/.test(ar)).toBe(false);
    expect(libraryDate("2026-11-02T09:00:00Z", "en", "UTC")).toBe("2 Nov 2026");
  });

  it("is not listed twice when the next page repeats it", () => {
    const shown = [item("a"), item("b")];
    expect(appendPage(shown, [item("b"), item("c")]).map((i) => i.id)).toEqual(["a", "b", "c"]);
  });
});
