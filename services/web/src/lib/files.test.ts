import { describe, expect, it } from "vitest";
import type { FileItem } from "@/lib/api/types";
import { EMPTY_FILE_FILTERS, appendFiles, fileSize, filesFiltered, filesQuery, uploadLink, withoutFile } from "./files";

const item = (id: string): FileItem => ({
  id,
  conversation_id: "0b7c1e2a-1111-4222-8333-944455556666",
  conversation_title: "هذا تحليل الدم",
  conversation_created_at: "2026-11-01T08:00:00Z",
  name: `${id}.pdf`,
  mime: "application/pdf",
  kind: "pdf",
  size: 2048,
  status: "indexed",
  pages: 2,
  created_at: "2026-11-01T08:01:00Z",
});

describe("the files page's query", () => {
  it("sends only the filters that are set, and the page size", () => {
    expect(filesQuery(EMPTY_FILE_FILTERS)).toBe("limit=30");
    const query = new URLSearchParams(
      filesQuery({ q: "  تحليل الدم ", kind: "pdf", status: "indexed", from: "2026-11-01", to: "2026-11-02" }),
    );
    expect(query.get("q")).toBe("تحليل الدم");
    expect(query.get("kind")).toBe("pdf");
    expect(query.get("status")).toBe("indexed");
    expect(query.get("since")).toMatch(/^2026-11-01T00:00:00[+-]\d{2}:\d{2}$/);
    // "to" includes the whole day: until is the next midnight.
    expect(query.get("until")).toMatch(/^2026-11-03T00:00:00[+-]\d{2}:\d{2}$/);
  });

  it("pages with before only without search words", () => {
    expect(new URLSearchParams(filesQuery(EMPTY_FILE_FILTERS, "2026-11-01T00:00:00Z")).get("before")).toBe("2026-11-01T00:00:00Z");
    expect(new URLSearchParams(filesQuery({ ...EMPTY_FILE_FILTERS, q: "عقد" }, "2026-11-01T00:00:00Z")).get("before")).toBeNull();
  });

  it("knows when a filter is set", () => {
    expect(filesFiltered(EMPTY_FILE_FILTERS)).toBe(false);
    expect(filesFiltered({ ...EMPTY_FILE_FILTERS, q: "   " })).toBe(false);
    expect(filesFiltered({ ...EMPTY_FILE_FILTERS, status: "failed" })).toBe(true);
  });
});

describe("a file row", () => {
  it("shows the size in Western digits, Arabic and English units", () => {
    expect(fileSize(900, "ar")).toBe("900 بايت");
    expect(fileSize(2048, "ar")).toBe("2.0 ك.ب");
    expect(fileSize(5 * 1024 * 1024, "en")).toBe("5.0 MB");
  });

  it("links back to its conversation at the upload", () => {
    expect(uploadLink(item("f1"))).toBe("/chat/0b7c1e2a-1111-4222-8333-944455556666?file=f1");
  });

  it("appends a next page without repeats and drops a deleted file", () => {
    expect(appendFiles([item("a"), item("b")], [item("b"), item("c")]).map((f) => f.id)).toEqual(["a", "b", "c"]);
    expect(withoutFile([item("a"), item("b")], "a").map((f) => f.id)).toEqual(["b"]);
  });
});
