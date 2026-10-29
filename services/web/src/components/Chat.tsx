"use client";

import { useLocale, useTranslations } from "next-intl";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type DragEvent, type FormEvent, type KeyboardEvent } from "react";
import type { AttachmentLimits, ChatTurnRequest, KbToggle, SkillView } from "@/lib/api/types";
import {
  POLL_INTERVAL_MS,
  type ComposerFile,
  checkBeforeUpload,
  getAttachment,
  getLimits,
  isBusy,
  isTerminal,
  turnAttachmentIds,
  uploadAttachment,
} from "@/lib/attachments";
import { type Turn, turnsFromHistory } from "@/lib/conversations";
import { GatewayError, getConversationMessages, isAbortError, listCapabilities, listSkills, streamTurn } from "@/lib/gateway";
import { loadSelection, saveSelection, selectedForTurn, toggled } from "@/lib/kbs";
import { DEFAULT_LOCALE, isLocale } from "@/lib/locale";
import { AUTO_OUTPUT, type OutputChoice, type ReportAction, loadOutput, saveOutput, skillAllowed, withOutput } from "@/lib/reports";
import {
  applyEvent,
  finalizeStream,
  initialAssistant,
  markAborted,
  markError,
  type AssistantMessage,
} from "@/lib/stream";
import { NO_SKILL, withSkill } from "@/lib/skills";
import { newUuid } from "@/lib/uuid";
import { AssistantMessageView } from "./AssistantMessageView";
import { AttachmentTray, LimitsHint } from "./AttachmentTray";
import { useAuth } from "./AuthProvider";
import { useRuntimeConfig } from "./ConfigProvider";
import { KbToggles } from "./KbToggles";
import { OutputPicker } from "./OutputPicker";
import { SkillPicker } from "./SkillPicker";

// The conversation's earlier messages load from the gateway when it opens (ADR-0193); new turns
// are added in memory as they stream. Every streamed event and each stream's end also go to
// `onReport`, which drives the report panel beside the chat (reports PR, I7).
export function Chat({
  conversationId,
  onTurnDone,
  onReport,
}: {
  conversationId: string;
  onTurnDone?: () => void;
  onReport?: (action: ReportAction) => void;
}) {
  const t = useTranslations();
  const rawLocale = useLocale();
  const locale = isLocale(rawLocale) ? rawLocale : DEFAULT_LOCALE;
  const config = useRuntimeConfig();
  const auth = useAuth();
  const pathname = usePathname();

  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [files, setFiles] = useState<ComposerFile[]>([]);
  const [limits, setLimits] = useState<AttachmentLimits | null>(null);
  const [sentFiles, setSentFiles] = useState(0);
  const [dragging, setDragging] = useState(false);
  const [history, setHistory] = useState<"loading" | "ready" | "failed">("loading");
  const [skills, setSkills] = useState<SkillView[]>([]);
  const [skill, setSkill] = useState(NO_SKILL);
  const [kbs, setKbs] = useState<KbToggle[]>([]);
  const [selectedKbs, setSelectedKbs] = useState<string[]>([]);
  const [output, setOutput] = useState<OutputChoice>(AUTO_OUTPUT);
  const controllerRef = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const pickerRef = useRef<HTMLInputElement>(null);

  const updateFile = useCallback((localId: string, fn: (f: ComposerFile) => ComposerFile) => {
    setFiles((all) => all.map((f) => (f.localId === localId ? fn(f) : f)));
  }, []);

  // The conversation's history, before any new turn (ADR-0193).
  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      const token = await auth.getAccessToken();
      if (!token) return;
      try {
        const loaded = await getConversationMessages({
          gatewayUrl: config.gatewayUrl,
          token,
          conversationId,
          signal: controller.signal,
        });
        // Idempotent: a reload replaces the history part and keeps turns sent meanwhile.
        setTurns((current) => [
          ...turnsFromHistory(loaded.messages),
          ...current.filter((turn) => !turn.id.startsWith("history-")),
        ]);
        setHistory("ready");
      } catch (error) {
        if (!isAbortError(error)) setHistory("failed");
      }
    })();
    return () => controller.abort();
  }, [auth, config.gatewayUrl, conversationId]);

  // The upload limits, shown before a file is chosen (D2).
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const token = await auth.getAccessToken();
      if (!token) return;
      try {
        const loaded = await getLimits({ gatewayUrl: config.gatewayUrl, token });
        if (!cancelled) setLimits(loaded);
      } catch {
        // the composer works without the hint; the gateway still enforces the limits
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [auth, config.gatewayUrl]);

  // The caller's skills for the picker (phase 06); without them the picker is not shown.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const token = await auth.getAccessToken();
      if (!token) return;
      try {
        const loaded = await listSkills({ gatewayUrl: config.gatewayUrl, token });
        if (!cancelled) setSkills(loaded.skills);
      } catch {
        // the composer works without the picker; the skill is then chosen automatically
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [auth, config.gatewayUrl]);

  // The knowledge bases the caller may toggle (phase 08, I3), and this conversation's toggles as
  // this browser kept them (README §13: persisted per conversation).
  useEffect(() => {
    setSelectedKbs(loadSelection(conversationId, typeof window === "undefined" ? undefined : window.localStorage));
    // The output choice is kept the same way (reports PR, D3).
    setOutput(loadOutput(conversationId, typeof window === "undefined" ? undefined : window.localStorage));
  }, [conversationId]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const token = await auth.getAccessToken();
      if (!token) return;
      try {
        const loaded = await listCapabilities({ gatewayUrl: config.gatewayUrl, token });
        if (!cancelled) setKbs(loaded.knowledge_bases);
      } catch {
        // the composer works without the toggles; no knowledge base is then used
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [auth, config.gatewayUrl]);

  function toggleKb(slug: string) {
    setSelectedKbs((current) => {
      const next = toggled(current, slug);
      saveSelection(conversationId, next, typeof window === "undefined" ? undefined : window.localStorage);
      return next;
    });
  }

  function chooseOutput(next: OutputChoice) {
    setOutput(next);
    saveOutput(conversationId, next, typeof window === "undefined" ? undefined : window.localStorage);
    // A report applies no skill (D1): a pick made before is dropped.
    if (!skillAllowed(next)) setSkill(NO_SKILL);
  }

  // Pending files are polled every 2 s until indexed or failed (ADR-0138).
  const pendingIds = files.filter((f) => f.attachment && !isTerminal(f.attachment.status)).map((f) => f.attachment!.id);
  const pendingKey = pendingIds.join(",");
  useEffect(() => {
    if (!pendingKey) return;
    const timer = window.setInterval(async () => {
      const token = await auth.getAccessToken();
      if (!token) return;
      for (const id of pendingKey.split(",")) {
        try {
          const latest = await getAttachment({ gatewayUrl: config.gatewayUrl, token, conversationId, id });
          setFiles((all) => all.map((f) => (f.attachment?.id === id ? { ...f, attachment: latest } : f)));
        } catch {
          // a missed poll is retried on the next tick
        }
      }
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [pendingKey, auth, config.gatewayUrl, conversationId]);

  // Takes an array, never the live FileList: the picker clears its list right after the change
  // event, before the token await below resolves, which left nothing to upload.
  async function addFiles(chosen: File[]) {
    const token = await auth.getAccessToken();
    if (!token) {
      await auth.login(pathname);
      return;
    }
    let held = sentFiles + files.filter((f) => !f.error).length;
    for (const file of chosen) {
      const localId = newUuid();
      const refusal = limits ? checkBeforeUpload(file, limits, held) : null;
      const entry: ComposerFile = { localId, name: file.name, size: file.size, progress: 0 };
      if (refusal) {
        setFiles((all) => [...all, { ...entry, error: { code: refusal } }]);
        continue;
      }
      held += 1;
      setFiles((all) => [...all, entry]);
      uploadAttachment({
        gatewayUrl: config.gatewayUrl,
        token,
        conversationId,
        file,
        onProgress: (progress) => updateFile(localId, (f) => ({ ...f, progress })),
      })
        .then((attachment) => updateFile(localId, (f) => ({ ...f, attachment, progress: 100 })))
        .catch((error: unknown) =>
          updateFile(localId, (f) => ({
            ...f,
            error: error instanceof GatewayError ? error.info : { code: "unknown" },
          })),
        );
    }
  }

  function removeFile(localId: string) {
    // Only a refused or unreadable file is dropped, from the composer only: the product deletes
    // no file (owner decision S4).
    setFiles((all) => all.filter((f) => f.localId !== localId));
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer.files.length) void addFiles(Array.from(e.dataTransfer.files));
  }

  const busy = files.some(isBusy);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [turns]);

  useEffect(() => () => controllerRef.current?.abort(), []);

  function updateAnswer(id: string, fn: (m: AssistantMessage) => AssistantMessage) {
    setTurns((all) => all.map((turn) => (turn.id === id ? { ...turn, answer: fn(turn.answer) } : turn)));
  }

  async function send() {
    const message = draft.trim();
    if (!message || streaming || busy) return;

    const id = newUuid();
    const attachmentIds = turnAttachmentIds(files);
    const names = files.filter((f) => f.attachment && f.attachment.status !== "failed").map((f) => f.name);
    setTurns((all) => [...all, { id, question: message, files: names, answer: initialAssistant() }]);
    setSentFiles((n) => n + attachmentIds.length);
    setFiles([]);
    setDraft("");
    const picked = skill;
    setSkill(NO_SKILL); // a pick goes with one turn only
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
      const base: ChatTurnRequest = {
        conversation_id: conversationId,
        message,
        // Only KBs still listed for the caller: a KB deleted or no longer readable is never sent (I7).
        selected_kbs: selectedForTurn(selectedKbs, kbs),
        attachment_ids: attachmentIds,
        locale,
      };
      // The output choice goes with every turn until changed; a report turn sends no skill (D1).
      const body = withOutput(skillAllowed(output) ? withSkill(base, picked, skills) : base, output);
      for await (const event of streamTurn({ gatewayUrl: config.gatewayUrl, token, body, signal: controller.signal })) {
        updateAnswer(id, (m) => applyEvent(m, event));
        onReport?.({ type: "event", event });
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
      // However the stream ended, a page not yet stored is dropped.
      onReport?.({ type: "end" });
      controllerRef.current = null;
      setStreaming(false);
      onTurnDone?.();
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
        {history === "failed" && <p className="mb-4 text-center text-sm text-error">{t("chat.historyFailed")}</p>}
        {turns.length === 0 && history === "loading" ? (
          <p className="mt-24 text-center text-sm text-muted">{t("chat.historyLoading")}</p>
        ) : turns.length === 0 ? (
          <div className="mt-24 text-center">
            <h1 className="text-2xl font-semibold text-fg">{t("chat.emptyTitle")}</h1>
            <p className="mt-2 text-muted">{t("chat.emptyHint")}</p>
          </div>
        ) : (
          <ol className="flex flex-col gap-6">
            {turns.map((turn) => (
              <li key={turn.id} className="flex flex-col gap-3">
                <div className="max-w-[85%] self-end rounded-2xl bg-primary px-4 py-3 text-on-primary shadow-sm">
                  <h2 className="sr-only">{t("chat.you")}</h2>
                  <p dir="auto" className="whitespace-pre-wrap leading-7">
                    {turn.question}
                  </p>
                  {turn.files.length > 0 && (
                    <ul className="mt-1.5 flex flex-wrap gap-1.5 text-xs text-on-primary">
                      {turn.files.map((name) => (
                        <li key={name} className="rounded bg-primary-hover/60 px-1.5 py-0.5">
                          <bdi dir="auto">{name}</bdi>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                <AssistantMessageView message={turn.answer} onOpenReport={(ref) => onReport?.({ type: "open", ref })} />
              </li>
            ))}
          </ol>
        )}
        <div ref={endRef} />
      </div>

      <form
        onSubmit={onSubmit}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={`border-t border-line bg-surface px-4 py-3 ${dragging ? "ring-2 ring-focus ring-inset" : ""}`}
      >
        {dragging && <p className="mb-2 text-center text-sm text-primary">{t("attachments.dropHere")}</p>}
        <AttachmentTray files={files} onRemove={removeFile} />
        <label htmlFor="composer" className="sr-only">
          {t("chat.inputLabel")}
        </label>
        <div className="flex items-end gap-2">
          <input
            ref={pickerRef}
            type="file"
            multiple
            hidden
            accept={limits ? limits.extensions.map((e) => `.${e}`).join(",") : undefined}
            onChange={(e) => {
              if (e.target.files?.length) void addFiles(Array.from(e.target.files));
              e.target.value = "";
            }}
          />
          <button
            type="button"
            onClick={() => pickerRef.current?.click()}
            disabled={streaming}
            aria-label={t("attachments.attach")}
            title={t("attachments.attach")}
            className="rounded-xl border border-line-strong px-3 py-2.5 text-muted hover:bg-surface-2 disabled:opacity-40"
          >
            <svg viewBox="0 0 24 24" className="h-5 w-5" aria-hidden fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M21 11.5l-8.5 8.5a5 5 0 01-7-7l9-9a3.5 3.5 0 015 5l-9 9a2 2 0 01-3-3l8.5-8.5" />
            </svg>
          </button>
          <textarea
            id="composer"
            dir="auto"
            rows={2}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            disabled={streaming}
            placeholder={t("chat.placeholder")}
            className="min-h-[3rem] flex-1 resize-none rounded-xl border border-line-strong px-3 py-2 leading-6 focus:border-focus focus:outline-none disabled:bg-surface-2"
          />
          {streaming ? (
            <button
              type="button"
              onClick={() => controllerRef.current?.abort()}
              className="rounded-xl bg-muted px-4 py-2.5 font-medium text-on-primary hover:bg-fg"
            >
              {t("chat.stop")}
            </button>
          ) : (
            <button
              type="submit"
              disabled={!draft.trim() || busy}
              title={busy ? t("chat.sendWaiting") : undefined}
              className="rounded-xl bg-primary px-4 py-2.5 font-medium text-on-primary hover:bg-primary-hover disabled:opacity-40"
            >
              {t("chat.send")}
            </button>
          )}
        </div>
        <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1.5">
          <KbToggles kbs={kbs} selected={selectedKbs} onToggle={toggleKb} disabled={streaming} />
          <OutputPicker value={output} onChange={chooseOutput} disabled={streaming} />
          <SkillPicker
            skills={skills}
            value={skill}
            onChange={setSkill}
            disabled={streaming || !skillAllowed(output)}
            disabledHint={skillAllowed(output) ? undefined : t("chat.skillOffForReport")}
          />
        </div>
        {busy && <p className="mt-1.5 text-xs text-warning">{t("chat.sendWaiting")}</p>}
        {limits && <div className="mt-1.5"><LimitsHint limits={limits} /></div>}
        <p className="mt-1.5 text-xs text-muted">
          {t.rich("chat.composerHint", {
            key: (chunks) => (
              <kbd className="rounded border border-line-strong bg-surface-2 px-1 font-mono text-[10px]">
                <bdi>{chunks}</bdi>
              </kbd>
            ),
          })}
        </p>
      </form>
    </div>
  );
}
