import { describe, expect, it } from "vitest";
import { turnsFromHistory } from "@/lib/conversations";
import { applyEvent, initialAssistant } from "@/lib/stream";
import { renderIn } from "@/test/render";
import { AssistantMessageView } from "./AssistantMessageView";
import { MessageMarkdown } from "./MessageMarkdown";

// Assistant answers render as Markdown, safely and right to left (ADR-0326, owner visual check of
// 2026-10-26).
const md = (value: string) => renderIn("ar", <MessageMarkdown text={value} />);

function token(seq: number, text: string) {
  return { type: "token", request_id: "r", turn_id: "t", seq, data: { text } } as const;
}

describe("Markdown in assistant messages", () => {
  it("renders emphasis and lists instead of their raw markers", () => {
    const html = md("**الإجازة السنوية** ثلاثون يومًا.\n\n- البند الأول\n- البند الثاني\n\n1. أولًا\n2. ثانيًا");
    expect(html).toContain("<strong>الإجازة السنوية</strong>");
    expect(html).toMatch(/<ul[^>]*>\s*<li[^>]*>البند الأول<\/li>\s*<li[^>]*>البند الثاني<\/li>\s*<\/ul>/);
    expect(html).toMatch(/<ol[^>]*>\s*<li[^>]*>أولًا<\/li>/);
    expect(html).not.toContain("**");
    expect(html).not.toMatch(/>\s*- /);
  });

  it("shows raw HTML as text and never as elements", () => {
    const html = md('قبل <script>alert(1)</script> و<b>غامق</b> <img src="https://x.example/a.png" onerror="alert(1)">');
    expect(html).not.toMatch(/<script|<b>|<img/);
    expect(html).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
    expect(html).toContain("&lt;b&gt;غامق&lt;/b&gt;");
  });

  it("keeps only http(s) links, opened in a new tab, and loads no image", () => {
    const html = md("[المصدر](https://example.com/a) و[خطر](javascript:alert(1)) ![صورة](https://x.example/p.png)");
    expect(html).toContain('<a href="https://example.com/a" target="_blank" rel="noopener noreferrer"');
    expect(html).not.toContain("javascript:");
    expect(html).toContain("خطر");
    expect(html).not.toContain("<img");
    expect(html).toContain("صورة");
  });

  it("lays out right to left: each block takes its own direction, lists indent on the start side, code stays LTR", () => {
    const html = md("فقرة عربية\n\n- بند\n\n> اقتباس\n\n`code`\n\n```\nx = 1\n```");
    expect(html).toMatch(/<p dir="auto"/);
    expect(html).toMatch(/<ul dir="auto" class="[^"]*\bps-6\b/);
    expect(html).not.toMatch(/\b(pl|ml)-\d/); // no physical left padding or margin
    expect(html).toMatch(/<blockquote dir="auto" class="[^"]*\bborder-s-4\b/);
    expect(html).toMatch(/<code dir="ltr"[^>]*>code<\/code>/);
    expect(html).toMatch(/<pre dir="ltr"/);
  });

  it("keeps a memo's single line breaks as lines", () => {
    const html = md("[اسم الجهة]\n[اسم الإدارة]\nمذكرة داخلية\nالرقم: [الرقم]");
    expect(html).toMatch(/<p dir="auto" class="[^"]*\bwhitespace-pre-line\b[^"]*">\[اسم الجهة\]\n\[اسم الإدارة\]\nمذكرة داخلية/);
  });

  it("renders tables from GitHub-flavoured Markdown", () => {
    const html = md("| البند | المدة |\n|---|---|\n| سنوية | 30 |");
    expect(html).toMatch(/<table dir="auto"[^>]*>\s*<thead>\s*<tr>\s*<th[^>]*>البند<\/th>/);
  });

  it("renders the live stream as Markdown, an unfinished marker included", () => {
    let message = initialAssistant();
    message = applyEvent(message, token(1, "**مهم**: "));
    message = applyEvent(message, token(2, "\n\n- أولًا\n- **ثا"));
    const html = renderIn("ar", <AssistantMessageView message={message} />);
    expect(html).toContain("<strong>مهم</strong>");
    expect(html).toMatch(/<li[^>]*>أولًا<\/li>/);
  });

  it("renders the history as Markdown too", () => {
    const [turn] = turnsFromHistory([
      { role: "user", text: "س", attachments: [], citations: [] },
      { role: "assistant", text: "**نعم**\n\n- بند", turn_id: "t1", attachments: [], citations: [] },
    ]);
    const html = renderIn("en", <AssistantMessageView message={turn!.answer} />);
    expect(html).toContain("<strong>نعم</strong>");
    expect(html).toMatch(/<ul dir="auto"[^>]*>\s*<li[^>]*>بند<\/li>\s*<\/ul>/);
    expect(html).not.toContain("**");
  });
});
