import { describe, expect, it } from "vitest";
import type { HistoryMessage } from "@/lib/api/types";
import { listDate, turnsFromHistory } from "./conversations";

const question = (text: string, files: string[] = []): HistoryMessage => ({
  role: "user",
  text,
  attachments: files.map((name, i) => ({ id: `f${i}`, name })),
  citations: [],
});
const reply = (text: string, pages: number[] = []): HistoryMessage => ({
  role: "assistant",
  text,
  turn_id: "0123456789abcdef0123456789abcdef",
  attachments: [],
  citations: pages.length ? [{ id: "f0", title: "لائحة.pdf", source: "doc", document_id: "f0", pages }] : [],
});

describe("the history as chat turns (ADR-0193)", () => {
  it("pairs each question with the reply after it, as a finished answer with its files and citations", () => {
    const turns = turnsFromHistory([question("لخص الملف", ["لائحة.pdf"]), reply("الملخص", [2, 3]), question("شكرًا"), reply("العفو")]);
    expect(turns.map((t) => [t.question, t.files, t.answer.text, t.answer.status])).toEqual([
      ["لخص الملف", ["لائحة.pdf"], "الملخص", "done"],
      ["شكرًا", [], "العفو", "done"],
    ]);
    expect(turns[0]!.answer.citations[0]!.pages).toEqual([2, 3]);
    expect(new Set(turns.map((t) => t.id)).size).toBe(2);
    expect(turns.every((t) => t.id.startsWith("history-"))).toBe(true);
  });

  it("drops a reply without its question and keeps an unanswered question", () => {
    const turns = turnsFromHistory([reply("يتيم"), question("سؤال"), reply("جواب"), reply("ثانٍ")]);
    expect(turns.map((t) => [t.question, t.answer.text])).toEqual([["سؤال", "جواب"]]);
    expect(turnsFromHistory([])).toEqual([]);
  });
});

describe("the list date", () => {
  const now = new Date("2026-10-15T12:00:00Z");
  it("shows a time today, a weekday this week and a date before that, in the locale", () => {
    expect(listDate("2026-10-15T09:30:00Z", "en", now)).toMatch(/\d/);
    expect(listDate("2026-10-13T09:30:00Z", "en", now)).toMatch(/day$/);
    expect(listDate("2026-09-01T09:30:00Z", "en", now)).toContain("2026");
    expect(listDate("2026-09-01T09:30:00Z", "ar", now)).toContain("سبتمبر");
  });
});
