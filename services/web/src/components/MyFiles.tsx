"use client";

import { useLocale, useTranslations } from "next-intl";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import type { FileItem } from "@/lib/api/types";
import { type ErrorInfo, resolveErrorMessage } from "@/lib/errors";
import {
  EMPTY_FILE_FILTERS,
  FILE_KINDS,
  FILE_STATUSES,
  type FileFilters,
  type FileKind,
  type FileStatus,
  appendFiles,
  fileSize,
  filesFiltered,
  filesQuery,
  uploadLink,
  withoutFile,
} from "@/lib/files";
import { GatewayError, deleteAttachment, downloadAttachment, isAbortError, listFiles } from "@/lib/gateway";
import { SEARCH_DEBOUNCE_MS, libraryDate } from "@/lib/library";
import { DEFAULT_LOCALE, isLocale } from "@/lib/locale";
import { saveFile } from "@/lib/reports";
import { useAuth } from "./AuthProvider";
import { useRuntimeConfig } from "./ConfigProvider";

export type FilesStatus = "loading" | "ready" | "failed";

const field =
  "mt-1 block w-full rounded-lg border border-line-strong bg-surface px-3 py-2 text-sm text-fg focus:border-focus focus:outline-none";
const action =
  "rounded-md px-2.5 py-1.5 text-sm text-fg ring-1 ring-line-strong hover:bg-surface-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-focus disabled:opacity-50";
const danger =
  "rounded-md px-2.5 py-1.5 text-sm text-error ring-1 ring-error-line hover:bg-error-bg focus:outline-none focus-visible:ring-2 focus-visible:ring-focus disabled:opacity-50";

const STATUS_STYLE: Record<FileStatus, string> = {
  indexed: "bg-success-bg text-success ring-success-line",
  pending: "bg-surface-2 text-muted ring-line",
  failed: "bg-error-bg text-error ring-error-line",
};

export type MyFilesViewProps = {
  filters: FileFilters;
  /** The filters the shown list was asked with (the search words after the debounce). */
  applied: FileFilters;
  items: FileItem[];
  status: FilesStatus;
  error: ErrorInfo | null;
  hasMore: boolean;
  loadingMore: boolean;
  /** The file being downloaded or deleted, if any. */
  busy: string | null;
  /** The file whose delete waits for confirmation, if any. */
  confirming: string | null;
  onFilters: (filters: FileFilters) => void;
  onClear: () => void;
  onRetry: () => void;
  onMore: () => void;
  onDownload: (item: FileItem) => void;
  onDelete: (item: FileItem) => void;
  onConfirm: (item: FileItem) => void;
  onCancel: () => void;
};

/** One file: its name, type, size, status, conversation and date, with its actions. */
function FileRow({
  item,
  busy,
  confirming,
  onDownload,
  onDelete,
  onConfirm,
  onCancel,
}: {
  item: FileItem;
  busy: boolean;
  confirming: boolean;
  onDownload: () => void;
  onDelete: () => void;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const t = useTranslations();
  const rawLocale = useLocale();
  const locale = isLocale(rawLocale) ? rawLocale : DEFAULT_LOCALE;
  const status = item.status as FileStatus;
  return (
    <li
      data-file={item.id}
      className={`flex flex-col gap-3 rounded-2xl bg-surface p-4 shadow-sm ring-1 sm:flex-row sm:items-start sm:justify-between ${
        confirming ? "ring-2 ring-error-line" : "ring-line"
      }`}
    >
      <div className="min-w-0 flex-1">
        <p className="max-w-full break-words font-medium text-fg">
          <bdi dir="auto">{item.name}</bdi>
        </p>
        <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
          <span className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-fg ring-1 ring-line">{item.kind.toUpperCase()}</span>
          <span>{fileSize(item.size, locale)}</span>
          <span aria-hidden>·</span>
          <span>{t("files.uploaded", { date: libraryDate(item.created_at, locale) })}</span>
          <span className={`rounded-full px-2 py-0.5 ring-1 ${STATUS_STYLE[status] ?? STATUS_STYLE.pending}`}>
            {t(`files.status.${status}`)}
          </span>
        </p>
        <p className="mt-1.5 text-sm text-fg">
          <span className="text-muted">{t("files.conversation")} </span>
          <bdi dir="auto">{item.conversation_title || t("files.untitled")}</bdi>
          {item.conversation_created_at && (
            <span className="text-muted"> · {libraryDate(item.conversation_created_at, locale)}</span>
          )}
        </p>
        {status === "failed" && item.reason && (
          <p className="mt-1 text-xs text-error">{t("files.failedReason", { reason: item.reason })}</p>
        )}
        {confirming && (
          <p role="alert" className="mt-2 text-sm text-error">
            {t("files.confirmText", { name: item.name })}
          </p>
        )}
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        {confirming ? (
          <>
            <button type="button" onClick={onConfirm} disabled={busy} className={danger}>
              {busy ? t("files.deleting") : t("files.confirm")}
            </button>
            <button type="button" onClick={onCancel} disabled={busy} className={action}>
              {t("files.cancel")}
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              onClick={onDownload}
              disabled={busy}
              aria-label={t("files.downloadLabel", { name: item.name })}
              className={action}
            >
              {busy ? t("library.loadingMore") : t("files.download")}
            </button>
            <button
              type="button"
              onClick={onDelete}
              disabled={busy}
              aria-label={t("files.deleteLabel", { name: item.name })}
              className={danger}
            >
              {t("files.delete")}
            </button>
            <Link href={uploadLink(item)} className="rounded-md px-2.5 py-1.5 text-sm text-primary hover:bg-surface-2">
              {t("files.openChat")}
            </Link>
          </>
        )}
      </div>
    </li>
  );
}

/** The page as it renders: the filters, then the list, its empty, error and loading states. */
export function MyFilesView(props: MyFilesViewProps) {
  const { filters, applied, items, status, error, hasMore, loadingMore, busy, confirming } = props;
  const t = useTranslations();
  const rawLocale = useLocale();
  const locale = isLocale(rawLocale) ? rawLocale : DEFAULT_LOCALE;
  const set = (patch: Partial<FileFilters>) => props.onFilters({ ...filters, ...patch });

  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-8">
      <h1 className="text-2xl font-semibold text-fg">{t("files.title")}</h1>
      <p className="mt-2 leading-7 text-muted">{t("files.description")}</p>

      <section
        aria-label={t("files.filters")}
        className="mt-6 grid gap-3 rounded-2xl bg-surface p-4 shadow-sm ring-1 ring-line sm:grid-cols-2"
      >
        <label className="block text-sm sm:col-span-2">
          <span className="text-fg">{t("files.search")}</span>
          <input
            type="search"
            dir="auto"
            value={filters.q}
            onChange={(e) => set({ q: e.target.value })}
            placeholder={t("files.searchPlaceholder")}
            className={field}
          />
        </label>
        <label className="block text-sm">
          <span className="text-fg">{t("files.kind")}</span>
          <select value={filters.kind} onChange={(e) => set({ kind: e.target.value as FileKind | "" })} className={field}>
            <option value="">{t("files.kindAll")}</option>
            {FILE_KINDS.map((kind) => (
              <option key={kind} value={kind}>
                {kind.toUpperCase()}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-sm">
          <span className="text-fg">{t("files.statusLabel")}</span>
          <select
            value={filters.status}
            onChange={(e) => set({ status: e.target.value as FileStatus | "" })}
            className={field}
          >
            <option value="">{t("files.statusAll")}</option>
            {FILE_STATUSES.map((value) => (
              <option key={value} value={value}>
                {t(`files.status.${value}`)}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-sm">
          <span className="text-fg">{t("library.from")}</span>
          <input
            type="date"
            value={filters.from}
            max={filters.to || undefined}
            onChange={(e) => set({ from: e.target.value })}
            className={field}
          />
        </label>
        <label className="block text-sm">
          <span className="text-fg">{t("library.to")}</span>
          <input
            type="date"
            value={filters.to}
            min={filters.from || undefined}
            onChange={(e) => set({ to: e.target.value })}
            className={field}
          />
        </label>
        <div className="flex items-end sm:col-span-2">
          <button type="button" onClick={props.onClear} disabled={!filesFiltered(filters)} className={action}>
            {t("library.clear")}
          </button>
        </div>
      </section>

      {error && (
        <p role="alert" className="mt-4 rounded-lg bg-error-bg px-3 py-2 text-sm text-error ring-1 ring-error-line">
          {resolveErrorMessage(error, locale, (key) => t(key))}
        </p>
      )}

      {status === "loading" && (
        <p role="status" className="mt-8 text-sm text-muted">
          {t("files.loading")}
        </p>
      )}

      {status === "failed" && (
        <button
          type="button"
          onClick={props.onRetry}
          className="mt-6 rounded-lg bg-primary px-4 py-2 font-medium text-on-primary hover:bg-primary-hover"
        >
          {t("library.retry")}
        </button>
      )}

      {status === "ready" && items.length === 0 && (
        <p className="mt-8 text-center text-muted">{filesFiltered(applied) ? t("files.noMatch") : t("files.empty")}</p>
      )}

      {status === "ready" && items.length > 0 && (
        <ul aria-label={t("files.list")} className="mt-6 flex flex-col gap-3">
          {items.map((item) => (
            <FileRow
              key={item.id}
              item={item}
              busy={busy === item.id}
              confirming={confirming === item.id}
              onDownload={() => props.onDownload(item)}
              onDelete={() => props.onDelete(item)}
              onConfirm={() => props.onConfirm(item)}
              onCancel={props.onCancel}
            />
          ))}
        </ul>
      )}

      {status === "ready" && hasMore && (
        <div className="mt-6 flex justify-center">
          <button type="button" onClick={props.onMore} disabled={loadingMore} className={action}>
            {loadingMore ? t("library.loadingMore") : t("library.loadMore")}
          </button>
        </div>
      )}
    </div>
  );
}

/** The latest value once it has stopped changing for `ms`. */
function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), ms);
    return () => window.clearTimeout(timer);
  }, [value, ms]);
  return settled;
}

/**
 * «ملفاتي» (my-files PR, owner decision 1): every file the caller uploaded in any conversation, through
 * GET /v1/files; download and delete through the conversation's attachment path, which the gateway checks and
 * audits again.
 */
export function MyFiles() {
  const { gatewayUrl } = useRuntimeConfig();
  const { getAccessToken, login } = useAuth();
  const pathname = usePathname();

  const [filters, setFilters] = useState<FileFilters>(EMPTY_FILE_FILTERS);
  const q = useDebounced(filters.q, SEARCH_DEBOUNCE_MS);
  const applied = useMemo<FileFilters>(
    () => ({ q, kind: filters.kind, status: filters.status, from: filters.from, to: filters.to }),
    [q, filters.kind, filters.status, filters.from, filters.to],
  );
  const query = filesQuery(applied);

  const [items, setItems] = useState<FileItem[]>([]);
  const [nextBefore, setNextBefore] = useState<string | null>(null);
  const [status, setStatus] = useState<FilesStatus>("loading");
  const [error, setError] = useState<ErrorInfo | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const more = useRef<AbortController | null>(null);

  function failed(failure: unknown) {
    setError(failure instanceof GatewayError ? failure.info : { code: "unknown" });
    if (failure instanceof GatewayError && failure.status === 401) void login(pathname);
  }

  // The first page, again whenever a filter changes; an older answer is dropped.
  useEffect(() => {
    const controller = new AbortController();
    more.current?.abort();
    setStatus("loading");
    setError(null);
    setLoadingMore(false);
    setConfirming(null);
    void (async () => {
      const token = await getAccessToken();
      if (controller.signal.aborted) return;
      if (!token) {
        await login(pathname);
        return;
      }
      try {
        const page = await listFiles({ gatewayUrl, token, query, signal: controller.signal });
        setItems(page.files);
        setNextBefore(page.next_before ?? null);
        setStatus("ready");
      } catch (failure) {
        if (isAbortError(failure)) return;
        setItems([]);
        setNextBefore(null);
        setStatus("failed");
        setError(failure instanceof GatewayError ? failure.info : { code: "unknown" });
        if (failure instanceof GatewayError && failure.status === 401) await login(pathname);
      }
    })();
    return () => controller.abort();
  }, [query, attempt, gatewayUrl, getAccessToken, login, pathname]);

  async function loadMore() {
    if (!nextBefore || loadingMore) return;
    const controller = new AbortController();
    more.current = controller;
    setLoadingMore(true);
    setError(null);
    try {
      const token = await getAccessToken();
      if (!token) {
        await login(pathname);
        return;
      }
      const page = await listFiles({ gatewayUrl, token, query: filesQuery(applied, nextBefore), signal: controller.signal });
      if (controller.signal.aborted) return;
      setItems((shown) => appendFiles(shown, page.files));
      setNextBefore(page.next_before ?? null);
    } catch (failure) {
      if (!isAbortError(failure)) failed(failure);
    } finally {
      if (more.current === controller) setLoadingMore(false);
    }
  }

  async function download(item: FileItem) {
    setBusy(item.id);
    setError(null);
    try {
      const token = await getAccessToken();
      if (!token) {
        await login(pathname);
        return;
      }
      const blob = await downloadAttachment({ gatewayUrl, token, conversationId: item.conversation_id, id: item.id });
      saveFile(blob, item.name);
    } catch (failure) {
      failed(failure);
    } finally {
      setBusy(null);
    }
  }

  async function remove(item: FileItem) {
    setBusy(item.id);
    setError(null);
    try {
      const token = await getAccessToken();
      if (!token) {
        await login(pathname);
        return;
      }
      await deleteAttachment({ gatewayUrl, token, conversationId: item.conversation_id, id: item.id });
      setItems((shown) => withoutFile(shown, item.id));
      setConfirming(null);
    } catch (failure) {
      failed(failure);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="h-full min-w-0 overflow-y-auto">
      <MyFilesView
        filters={filters}
        applied={applied}
        items={items}
        status={status}
        error={error}
        hasMore={nextBefore !== null && !applied.q.trim()}
        loadingMore={loadingMore}
        busy={busy}
        confirming={confirming}
        onFilters={setFilters}
        onClear={() => setFilters(EMPTY_FILE_FILTERS)}
        onRetry={() => setAttempt((n) => n + 1)}
        onMore={() => void loadMore()}
        onDownload={(item) => void download(item)}
        onDelete={(item) => setConfirming(item.id)}
        onConfirm={(item) => void remove(item)}
        onCancel={() => setConfirming(null)}
      />
    </div>
  );
}
