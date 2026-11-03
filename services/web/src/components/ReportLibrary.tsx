"use client";

import { useLocale, useTranslations } from "next-intl";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import type { KbToggle, LibraryItem } from "@/lib/api/types";
import { type ErrorInfo, resolveErrorMessage } from "@/lib/errors";
import { GatewayError, downloadReport, isAbortError, listCapabilities, listLibrary } from "@/lib/gateway";
import {
  EMPTY_FILTERS,
  type LibraryFilters,
  SEARCH_DEBOUNCE_MS,
  type SourceOption,
  appendPage,
  filtered,
  libraryDate,
  libraryQuery,
  rowSources,
  sourceOptions,
} from "@/lib/library";
import { DEFAULT_LOCALE, isLocale } from "@/lib/locale";
import { CLOSED_PANEL, reportFileName, reportPanel, saveFile } from "@/lib/reports";
import { useAuth } from "./AuthProvider";
import { useRuntimeConfig } from "./ConfigProvider";
import { ReportPanel } from "./ReportPanel";

export type LibraryStatus = "loading" | "ready" | "failed";

const field =
  "mt-1 block w-full rounded-lg border border-line-strong bg-surface px-3 py-2 text-sm text-fg focus:border-focus focus:outline-none";
const action =
  "rounded-md px-2.5 py-1.5 text-sm text-fg ring-1 ring-line-strong hover:bg-surface-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-focus disabled:opacity-50";

export type ReportLibraryViewProps = {
  filters: LibraryFilters;
  /** The filters the shown list was asked with (the search words after the debounce). */
  applied: LibraryFilters;
  options: SourceOption[];
  items: LibraryItem[];
  status: LibraryStatus;
  error: ErrorInfo | null;
  hasMore: boolean;
  loadingMore: boolean;
  downloading: string | null;
  openId: string | null;
  onFilters: (filters: LibraryFilters) => void;
  onClear: () => void;
  onRetry: () => void;
  onMore: () => void;
  onOpen: (item: LibraryItem) => void;
  onDownload: (item: LibraryItem) => void;
};

/** One report in the list: title, version, date, sources and subject, with its actions. */
function LibraryRow({
  item,
  searched,
  open,
  downloading,
  onOpen,
  onDownload,
}: {
  item: LibraryItem;
  searched: boolean;
  open: boolean;
  downloading: boolean;
  onOpen: () => void;
  onDownload: () => void;
}) {
  const t = useTranslations();
  const rawLocale = useLocale();
  const locale = isLocale(rawLocale) ? rawLocale : DEFAULT_LOCALE;
  const sources = rowSources(item, t("library.sourceGeneral"));
  return (
    <li
      data-library-report={item.id}
      className={`flex flex-col gap-3 rounded-2xl bg-surface p-4 shadow-sm ring-1 sm:flex-row sm:items-start sm:justify-between ${
        open ? "ring-2 ring-focus" : "ring-line"
      }`}
    >
      <div className="min-w-0 flex-1">
        <button
          type="button"
          onClick={onOpen}
          aria-label={t("reports.open", { title: item.title })}
          className="max-w-full text-start font-medium text-fg hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-focus"
        >
          <bdi dir="auto">{item.title}</bdi>
        </button>
        <p className="mt-1 flex flex-wrap gap-x-2 text-xs text-muted">
          <span>{t("reports.version", { version: String(item.latest_version) })}</span>
          <span aria-hidden>·</span>
          <span>{t("library.updated", { date: libraryDate(item.updated_at, locale) })}</span>
        </p>
        {item.subject && (
          <p dir="auto" className="mt-1.5 text-sm leading-6 text-fg">
            {item.subject}
          </p>
        )}
        {sources.length > 0 && (
          <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs">
            <span className="text-muted">{t("library.sources")}</span>
            {sources.map((name) => (
              <span key={name} className="rounded-full bg-surface-2 px-2 py-0.5 text-fg ring-1 ring-line">
                <bdi dir="auto">{name}</bdi>
              </span>
            ))}
          </div>
        )}
        {searched && item.matched_version !== item.latest_version && (
          <p className="mt-1.5 text-xs text-subtle">{t("library.matched", { version: String(item.matched_version) })}</p>
        )}
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        <button type="button" onClick={onOpen} className={action}>
          {t("library.open")}
        </button>
        <button
          type="button"
          onClick={onDownload}
          disabled={downloading}
          aria-label={t("library.downloadLabel", { title: item.title })}
          className={action}
        >
          {downloading ? t("library.loadingMore") : t("library.download")}
        </button>
        <Link href={`/chat/${item.conversation_id}`} className="rounded-md px-2.5 py-1.5 text-sm text-primary hover:bg-surface-2">
          {t("library.openChat")}
        </Link>
      </div>
    </li>
  );
}

/** The library as it renders: the filters, then the list, its empty, error and loading states. */
export function ReportLibraryView(props: ReportLibraryViewProps) {
  const { filters, applied, options, items, status, error, hasMore, loadingMore, downloading, openId } = props;
  const t = useTranslations();
  const rawLocale = useLocale();
  const locale = isLocale(rawLocale) ? rawLocale : DEFAULT_LOCALE;
  const set = (patch: Partial<LibraryFilters>) => props.onFilters({ ...filters, ...patch });
  const searched = Boolean(applied.q.trim());

  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-8">
      <h1 className="text-2xl font-semibold text-fg">{t("library.title")}</h1>
      <p className="mt-2 leading-7 text-muted">{t("library.description")}</p>

      <section
        aria-label={t("library.filters")}
        className="mt-6 grid gap-3 rounded-2xl bg-surface p-4 shadow-sm ring-1 ring-line sm:grid-cols-2"
      >
        <label className="block text-sm sm:col-span-2">
          <span className="text-fg">{t("library.search")}</span>
          <input
            type="search"
            dir="auto"
            value={filters.q}
            onChange={(e) => set({ q: e.target.value })}
            placeholder={t("library.searchPlaceholder")}
            className={field}
          />
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
        <label className="block text-sm">
          <span className="text-fg">{t("library.source")}</span>
          <select value={filters.source} onChange={(e) => set({ source: e.target.value })} className={field}>
            {options.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <div className="flex items-end">
          <button type="button" onClick={props.onClear} disabled={!filtered(filters)} className={action}>
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
          {t("library.loading")}
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
        <p className="mt-8 text-center text-muted">{filtered(applied) ? t("library.noMatch") : t("library.empty")}</p>
      )}

      {status === "ready" && items.length > 0 && (
        <ul aria-label={t("library.list")} className="mt-6 flex flex-col gap-3">
          {items.map((item) => (
            <LibraryRow
              key={item.id}
              item={item}
              searched={searched}
              open={openId === item.id}
              downloading={downloading === item.id}
              onOpen={() => props.onOpen(item)}
              onDownload={() => props.onDownload(item)}
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
 * «تقاريري» (report library PR): the caller's own reports across all their conversations, searched
 * and filtered through GET /v1/reports; a report opens in the same side panel the chat uses.
 */
export function ReportLibrary() {
  const t = useTranslations();
  const rawLocale = useLocale();
  const locale = isLocale(rawLocale) ? rawLocale : DEFAULT_LOCALE;
  const { gatewayUrl } = useRuntimeConfig();
  const { getAccessToken, login } = useAuth();
  const pathname = usePathname();

  const [filters, setFilters] = useState<LibraryFilters>(EMPTY_FILTERS);
  const q = useDebounced(filters.q, SEARCH_DEBOUNCE_MS);
  const applied = useMemo<LibraryFilters>(
    () => ({ q, from: filters.from, to: filters.to, source: filters.source }),
    [q, filters.from, filters.to, filters.source],
  );
  const query = libraryQuery(applied);

  const [items, setItems] = useState<LibraryItem[]>([]);
  const [nextBefore, setNextBefore] = useState<string | null>(null);
  const [status, setStatus] = useState<LibraryStatus>("loading");
  const [error, setError] = useState<ErrorInfo | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const [downloading, setDownloading] = useState<string | null>(null);
  const [kbs, setKbs] = useState<KbToggle[]>([]);
  const [panel, dispatch] = useReducer(reportPanel, CLOSED_PANEL);
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
    void (async () => {
      const token = await getAccessToken();
      if (controller.signal.aborted) return;
      if (!token) {
        await login(pathname);
        return;
      }
      try {
        const page = await listLibrary({ gatewayUrl, token, query, signal: controller.signal });
        setItems(page.reports);
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

  // The source filter lists the KBs the caller may read; without them it still offers the rest.
  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      const token = await getAccessToken();
      if (!token) return;
      try {
        const loaded = await listCapabilities({ gatewayUrl, token, signal: controller.signal });
        setKbs(loaded.knowledge_bases);
      } catch {
        // the filter keeps all, general knowledge and attached files
      }
    })();
    return () => controller.abort();
  }, [gatewayUrl, getAccessToken]);

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
      const page = await listLibrary({ gatewayUrl, token, query: libraryQuery(applied, nextBefore), signal: controller.signal });
      if (controller.signal.aborted) return;
      setItems((shown) => appendPage(shown, page.reports));
      setNextBefore(page.next_before ?? null);
    } catch (failure) {
      if (!isAbortError(failure)) failed(failure);
    } finally {
      if (more.current === controller) setLoadingMore(false);
    }
  }

  async function download(item: LibraryItem) {
    setDownloading(item.id);
    setError(null);
    try {
      const token = await getAccessToken();
      if (!token) {
        await login(pathname);
        return;
      }
      const blob = await downloadReport({ gatewayUrl, token, id: item.id, version: item.latest_version });
      saveFile(blob, reportFileName(item.id, item.latest_version));
    } catch (failure) {
      failed(failure);
    } finally {
      setDownloading(null);
    }
  }

  const options = sourceOptions(kbs, locale, (key) => t(key));

  return (
    <div className="flex h-full min-w-0">
      <div className="min-w-0 flex-1 overflow-y-auto">
        <ReportLibraryView
          filters={filters}
          applied={applied}
          options={options}
          items={items}
          status={status}
          error={error}
          hasMore={nextBefore !== null && !applied.q.trim()}
          loadingMore={loadingMore}
          downloading={downloading}
          openId={panel.open ? panel.id : null}
          onFilters={setFilters}
          onClear={() => setFilters(EMPTY_FILTERS)}
          onRetry={() => setAttempt((n) => n + 1)}
          onMore={() => void loadMore()}
          onOpen={(item) => dispatch({ type: "open", ref: { id: item.id, title: item.title, version: item.matched_version } })}
          onDownload={(item) => void download(item)}
        />
      </div>
      <ReportPanel state={panel} onAction={dispatch} />
    </div>
  );
}
