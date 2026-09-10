"use client";

import { useLocale, useTranslations } from "next-intl";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import type { ChatTurnRequest } from "@/lib/api/types";
import { GatewayError, isAbortError, streamTurn } from "@/lib/gateway";
import { DEFAULT_LOCALE, isLocale } from "@/lib/locale";
import {
  applyEvent,
  finalizeStream,
  initialAssistant,
  markAborted,
  markError,
  type AssistantMessage,
} from "@/lib/stream";
import { newUuid } from "@/lib/uuid";
import { AssistantMessageView } from "./AssistantMessageView";
import { useAuth } from "./AuthProvider";
import { useRuntimeConfig } from "./ConfigProvider";

type Turn = { id: string; question: string; answer: AssistantMessage };

// Conversation history lives in memory only; persistence arrives with state in phase 02.
export function Chat({ conversationId }: { conversationId: string }) {
  const t = useTranslations();
  const rawLocale = useLocale();
  const locale = isLocale(rawLocale) ? rawLocale : DEFAULT_LOCALE;
  const config = useRuntimeConfig();
  const auth = useAuth();
  const pathname = usePathname();

  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [turns]);

  useEffect(() => () => controllerRef.current?.abort(), []);

  function updateAnswer(id: string, fn: (m: AssistantMessage) => AssistantMessage) {
    setTurns((all) => all.map((turn) => (turn.id === id ? { ...turn, answer: fn(turn.answer) } : turn)));
  }

  async function send() {
    const message = draft.trim();
    if (!message || streaming) return;

    const id = newUuid();
    setTurns((all) => [...all, { id, question: message, answer: initialAssistant() }]);
    setDraft("");
    setStreaming(true);
    const controller = new AbortController();
    controllerRef.current = controller;

    try {
      const token = await auth.getAccessToken();
      if (!token) {
        updateAnswer(id, (m) => markError(m, { code: "unauthorized" }));
        await auth.login(pathname);
        return;
      }
      const body: ChatTurnRequest = {
        conversation_id: conversationId,
        message,
        selected_kbs: [],
        attachments: [],
        locale,
      };
      for await (const event of streamTurn({ gatewayUrl: config.gatewayUrl, token, body, signal: controller.signal })) {
        updateAnswer(id, (m) => applyEvent(m, event));
      }
      updateAnswer(id, finalizeStream);
    } catch (error) {
      if (isAbortError(error)) {
        updateAnswer(id, markAborted);
      } else if (error instanceof GatewayError) {
        updateAnswer(id, (m) => markError(m, error.info));
        if (error.status === 401) await auth.login(pathname);
      } else {
        updateAnswer(id, (m) => markError(m, { code: "unknown" }));
      }
    } finally {
      controllerRef.current = null;
      setStreaming(false);
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void send();
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void send();
    }
  }

  return (
    <div className="mx-auto flex h-full w-full max-w-3xl flex-col">
      <div className="flex-1 overflow-y-auto px-4 py-6" aria-busy={streaming}>
        {turns.length === 0 ? (
          <div className="mt-24 text-center">
            <h1 className="text-2xl font-semibold text-slate-800">{t("chat.emptyTitle")}</h1>
            <p className="mt-2 text-slate-500">{t("chat.emptyHint")}</p>
          </div>
        ) : (
          <ol className="flex flex-col gap-6">
            {turns.map((turn) => (
              <li key={turn.id} className="flex flex-col gap-3">
                <div className="max-w-[85%] self-end rounded-2xl bg-emerald-700 px-4 py-3 text-white shadow-sm">
                  <h2 className="sr-only">{t("chat.you")}</h2>
                  <p dir="auto" className="whitespace-pre-wrap leading-7">
                    {turn.question}
                  </p>
                </div>
                <AssistantMessageView message={turn.answer} />
              </li>
            ))}
          </ol>
        )}
        <div ref={endRef} />
      </div>

      <form onSubmit={onSubmit} className="border-t border-slate-200 bg-white px-4 py-3">
        <label htmlFor="composer" className="sr-only">
          {t("chat.inputLabel")}
        </label>
        <div className="flex items-end gap-2">
          <textarea
            id="composer"
            dir="auto"
            rows={2}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            disabled={streaming}
            placeholder={t("chat.placeholder")}
            className="min-h-[3rem] flex-1 resize-none rounded-xl border border-slate-300 px-3 py-2 leading-6 focus:border-emerald-600 focus:outline-none disabled:bg-slate-50"
          />
          {streaming ? (
            <button
              type="button"
              onClick={() => controllerRef.current?.abort()}
              className="rounded-xl bg-slate-700 px-4 py-2.5 font-medium text-white hover:bg-slate-800"
            >
              {t("chat.stop")}
            </button>
          ) : (
            <button
              type="submit"
              disabled={!draft.trim()}
              className="rounded-xl bg-emerald-700 px-4 py-2.5 font-medium text-white hover:bg-emerald-800 disabled:opacity-40"
            >
              {t("chat.send")}
            </button>
          )}
        </div>
        <p className="mt-1.5 text-xs text-slate-500">
          {t.rich("chat.composerHint", {
            key: (chunks) => (
              <kbd className="rounded border border-slate-300 bg-slate-50 px-1 font-mono text-[10px]">
                <bdi>{chunks}</bdi>
              </kbd>
            ),
          })}
        </p>
      </form>
    </div>
  );
}
