import { describe, expect, it } from "vitest";
import type { AttachmentLimits, AttachmentView, Citation } from "@/lib/api/types";
import {
  type ComposerFile,
  checkBeforeUpload,
  extensionOf,
  isBusy,
  isDismissible,
  reasonKey,
  turnAttachmentIds,
} from "@/lib/attachments";
import { renderIn } from "@/test/render";
import { CitationList } from "./AssistantMessageView";
import { AttachmentChip, LimitsHint } from "./AttachmentTray";

const LIMITS: AttachmentLimits = {
  max_file_bytes: 20 * 1024 * 1024,
  max_files_per_conversation: 10,
  max_pdf_pages: 300,
  extensions: ["docx", "markdown", "md", "pdf", "txt", "xlsx"],
};

function attachment(status: string, reason: string | null = null): AttachmentView {
  return {
    id: "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d",
    conversation_id: "7d1f2c3b-4a5e-4f60-8172-93a4b5c6d7e8",
    name: "لائحة الإجازات.pdf",
    mime: "application/pdf",
    kind: "pdf",
    size: 1234,
    status,
    reason,
    created_at: "2026-10-11T10:00:00Z",
  } as AttachmentView;
}

function file(extra: Partial<ComposerFile> = {}): ComposerFile {
  return { localId: "l1", name: "لائحة الإجازات.pdf", size: 1234, progress: 40, ...extra };
}

/** Visible text: tags dropped, so attributes (titles) do not count. */
function text(html: string): string {
  return html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}

/** No word mixes Arabic and Latin letters (D2: no Latin token inside an Arabic sentence). */
function hasMixedToken(value: string): boolean {
  return value.split(/\s+/).some((w) => /[؀-ۿ]/.test(w) && /[A-Za-z]/.test(w));
}

describe("checks before upload (D2)", () => {
  it("refuses a type, a size and a full conversation before any byte leaves", () => {
    expect(checkBeforeUpload({ name: "برنامج.exe", size: 10 }, LIMITS, 0)).toBe("file_type_unsupported");
    expect(checkBeforeUpload({ name: "كبير.pdf", size: LIMITS.max_file_bytes + 1 }, LIMITS, 0)).toBe("file_too_large");
    expect(checkBeforeUpload({ name: "ملف.PDF", size: 10 }, LIMITS, 10)).toBe("attachment_limit");
    expect(checkBeforeUpload({ name: "ملف.PDF", size: 10 }, LIMITS, 9)).toBeNull();
    expect(extensionOf("تقرير.ربع.XLSX")).toBe("xlsx");
  });

  it("lets the composer drop only a refused or unreadable file: the product deletes none (S4)", () => {
    expect(isDismissible(file({ error: { code: "file_too_large" } }))).toBe(true);
    expect(isDismissible(file({ attachment: attachment("failed", "corrupt_file") }))).toBe(true);
    expect(isDismissible(file({ attachment: attachment("indexed") }))).toBe(false);
    expect(isDismissible(file({ attachment: attachment("pending") }))).toBe(false);
    expect(isDismissible(file())).toBe(false); // still uploading
  });

  it("blocks sending while a file uploads or is pending, never for an indexed or failed one", () => {
    expect(isBusy(file())).toBe(true);
    expect(isBusy(file({ attachment: attachment("pending") }))).toBe(true);
    expect(isBusy(file({ attachment: attachment("indexed") }))).toBe(false);
    expect(isBusy(file({ attachment: attachment("failed", "scanned_unsupported") }))).toBe(false);
    expect(isBusy(file({ error: { code: "file_too_large" } }))).toBe(false);
  });

  it("sends the ids of registered files that did not fail", () => {
    const files = [
      file({ localId: "a", attachment: { ...attachment("indexed"), id: "a1" } }),
      file({ localId: "b", attachment: { ...attachment("failed", "corrupt_file"), id: "b1" } }),
      file({ localId: "c", error: { code: "file_too_large" } }),
    ];
    expect(turnAttachmentIds(files)).toEqual(["a1"]);
  });

  it("maps an unknown reason to the generic message", () => {
    const known = (key: string) => key !== "attachments.reasons.new_reason";
    expect(reasonKey("scanned_unsupported", known)).toBe("attachments.reasons.scanned_unsupported");
    expect(reasonKey("new_reason", known)).toBe("attachments.reasons.unknown");
    expect(reasonKey(null, known)).toBe("attachments.reasons.unknown");
  });
});

describe("the status badge (I10)", () => {
  it.each([
    ["pending", "قيد المعالجة"],
    ["indexed", "جاهز"],
    ["failed", "تعذّرت القراءة"],
  ])("shows %s in Arabic with the file name isolated", (status, label) => {
    const html = renderIn("ar", <AttachmentChip file={file({ attachment: attachment(status, status === "failed" ? "scanned_unsupported" : null) })} />);
    expect(html).toContain(label);
    expect(html).toMatch(/<bdi dir="auto"[^>]*>لائحة الإجازات\.pdf<\/bdi>/);
    if (status === "failed") expect(html).toContain("الملف صورة ممسوحة بلا نص قابل للقراءة");
    expect(hasMixedToken(text(html).replace("لائحة الإجازات.pdf", ""))).toBe(false);
  });

  it("shows upload progress before the file is registered", () => {
    const html = renderIn("ar", <AttachmentChip file={file({ progress: 40 })} />);
    expect(html).toContain("جارٍ الرفع 40٪");
  });

  it("shows a refusal's Arabic message", () => {
    const html = renderIn("ar", <AttachmentChip file={file({ error: { code: "file_too_large" } })} />);
    expect(html).toContain("حجم الملف أكبر من الحد المسموح به.");
  });
});

describe("the limits line (D2)", () => {
  it("names the limits in Arabic words and the locale's digits, without Latin tokens", () => {
    const html = renderIn("ar", <LimitsHint limits={LIMITS} />);
    expect(text(html)).toContain("حتى 10 ملفات في المحادثة، و20 ميغابايت للملف، و300 صفحة");
    expect(text(html)).toContain("بي دي إف");
    expect(/[A-Za-z]/.test(text(html))).toBe(false);
  });

  it("reads in English in the English locale", () => {
    expect(text(renderIn("en", <LimitsHint limits={LIMITS} />))).toContain("Up to 10 files per conversation, 20 MB per file");
  });
});

describe("the citation line (ADR-0140)", () => {
  const cited: Citation[] = [
    { id: "d1", title: "لائحة الإجازات.pdf", source: "doc", document_id: "d1", pages: [2, 5] },
    { id: "d2", title: "دليل السياسات.docx", source: "doc", document_id: "d2", pages: [], locator: "الباب الثاني: العمل عن بعد" },
  ];

  it("shows each file name with its pages, or its section when it has none", () => {
    const html = renderIn("ar", <CitationList citations={cited} />);
    expect(html).toContain("المصدر");
    expect(html).toMatch(/<bdi dir="auto"[^>]*>لائحة الإجازات\.pdf<\/bdi>/);
    expect(text(html)).toContain("الصفحتان 2 و5");
    expect(html).toContain("الباب الثاني: العمل عن بعد");
  });

  it("uses the singular for one page, in both locales", () => {
    const one: Citation[] = [{ id: "d1", title: "تعميم.pdf", source: "doc", pages: [3] }];
    expect(text(renderIn("ar", <CitationList citations={one} />))).toContain("الصفحة 3");
    expect(text(renderIn("en", <CitationList citations={one} />))).toContain("page 3");
  });
});

describe("a partly scanned PDF (D7, ADR-0201)", () => {
  function warned(count: number): ComposerFile {
    const view = { ...attachment("indexed"), warnings: [{ code: "pages_without_text", count }] } as AttachmentView;
    return file({ attachment: view });
  }

  it("says how many pages were not read, with the Arabic plural and the app's Arabic numbers", () => {
    const ar = new Intl.NumberFormat("ar");
    const shown = text(renderIn("ar", <AttachmentChip file={warned(5)} />));
    expect(shown).toContain(`جاهز — ${ar.format(5)} صفحات صور لم تُقرأ`);
    expect(shown).toContain("لم تُقرأ لأن قراءة الصور غير مدعومة بعد");
    expect(hasMixedToken(shown.replace("لائحة الإجازات.pdf", ""))).toBe(false); // the file name is the user's
    expect(text(renderIn("ar", <AttachmentChip file={warned(1)} />))).toContain("جاهز — صفحة صورة واحدة لم تُقرأ");
    expect(text(renderIn("ar", <AttachmentChip file={warned(2)} />))).toContain("جاهز — صفحتا صور لم تُقرآ");
    expect(text(renderIn("ar", <AttachmentChip file={warned(11)} />))).toContain(`جاهز — ${ar.format(11)} صفحة صور لم تُقرأ`);
  });

  it("says it in English too, and nothing for a file read whole", () => {
    expect(text(renderIn("en", <AttachmentChip file={warned(5)} />))).toContain("Ready — 5 image pages not read");
    const whole = text(renderIn("ar", <AttachmentChip file={file({ attachment: attachment("indexed") })} />));
    expect(whole).toContain("جاهز");
    expect(whole).not.toContain("لم تُقرأ");
  });
});
