// A conversation's history as chat turns (ADR-0193). Pure, so the pairing is tested without a
// browser: each question opens a turn and the reply after it is that turn's finished answer.
import type { Citation, HistoryMessage } from "@/lib/api/types";
import type { AssistantMessage } from "./stream";

export type Turn = { id: string; question: string; files: string[]; answer: AssistantMessage };

function finished(text: string, citations: Citation[]): AssistantMessage {
  return { text, status: "done", receivedTokens: text.length > 0, chips: [], citations };
}

export function turnsFromHistory(messages: HistoryMessage[]): Turn[] {
  const turns: Turn[] = [];
  messages.forEach((message, index) => {
    if (message.role === "user") {
      const files = message.attachments.map((file) => file.name);
      turns.push({ id: `history-${index}`, question: message.text, files, answer: finished("", []) });
      return;
    }
    const open = turns.at(-1);
    // A reply without its question is never stored (persist_state writes both); drop it.
    if (!open || open.answer.text) return;
    open.answer = finished(message.text, message.citations as Citation[]);
  });
  return turns;
}

/** A date for the list: the time today, the day this week, the date before that. */
export function listDate(iso: string, locale: string, now: Date = new Date()): string {
  const at = new Date(iso);
  const sameDay = at.toDateString() === now.toDateString();
  const days = (now.getTime() - at.getTime()) / 86_400_000;
  const tag = locale === "ar" ? "ar" : "en";
  if (sameDay) return new Intl.DateTimeFormat(tag, { hour: "numeric", minute: "2-digit" }).format(at);
  if (days < 7) return new Intl.DateTimeFormat(tag, { weekday: "long" }).format(at);
  return new Intl.DateTimeFormat(tag, { day: "numeric", month: "short", year: "numeric" }).format(at);
}
