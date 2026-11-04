// A conversation's history as chat turns (ADR-0193). Pure, so the pairing is tested without a
// browser: each question opens a turn and the reply after it is that turn's finished answer.
import type { ArtifactRef, Citation, HistoryMessage } from "@/lib/api/types";
import type { AppliedSkill, AssistantMessage } from "./stream";

export type Turn = {
  id: string;
  question: string;
  files: string[];
  answer: AssistantMessage;
  /** The ids of the files the question carried (history only): «ملفاتي» opens a conversation at one. */
  fileIds?: string[];
};

function finished(
  text: string,
  citations: Citation[],
  skill?: AppliedSkill | null,
  artifact?: ArtifactRef | null,
): AssistantMessage {
  const message: AssistantMessage = { text, status: "done", receivedTokens: text.length > 0, chips: [], citations };
  return { ...message, ...(skill ? { skill } : {}), ...(artifact ? { artifact } : {}) };
}

export function turnsFromHistory(messages: HistoryMessage[]): Turn[] {
  const turns: Turn[] = [];
  messages.forEach((message, index) => {
    if (message.role === "user") {
      const files = message.attachments.map((file) => file.name);
      const fileIds = message.attachments.map((file) => file.id);
      turns.push({ id: `history-${index}`, question: message.text, files, fileIds, answer: finished("", []) });
      return;
    }
    const open = turns.at(-1);
    // A reply without its question is never stored (persist_state writes both); drop it.
    if (!open || open.answer.text) return;
    // A reply that wrote a report carries it, so its card reopens the panel (reports PR).
    open.answer = finished(message.text, message.citations as Citation[], message.skill, message.artifact);
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

/** The turn whose question carried the file `id`, as the link from «ملفاتي» names it (my-files PR). */
export function turnWithFile(turns: readonly Turn[], id: string | null): Turn | undefined {
  return id ? turns.find((turn) => turn.fileIds?.includes(id)) : undefined;
}

/** The `file` parameter of a chat URL's query, when it is an id. */
export function fileParam(search: string): string | null {
  const id = new URLSearchParams(search).get("file");
  return id && /^[0-9a-f-]{36}$/i.test(id) ? id : null;
}
