"use client";

import { useLocale, useTranslations } from "next-intl";
import { useEffect, useRef, useState, type KeyboardEvent, type RefObject } from "react";
import type { ArtifactRef, ReportVersionSummary } from "@/lib/api/types";
import { type ErrorInfo, resolveErrorMessage } from "@/lib/errors";
import { GatewayError, createReportView, downloadReport, getReport, getReportVersion, isAbortError } from "@/lib/gateway";
import { DEFAULT_LOCALE, isLocale, type Locale } from "@/lib/locale";
import {
  FRAME_SANDBOX,
  type FrameSource,
  type ReportAction,
  type ReportPanelState,
  frameSource,
  kitUrl,
  newestFirst,
  reportFileName,
} from "@/lib/reports";
import { pageFormat } from "@/lib/settings";
import { useAuth } from "./AuthProvider";
import { useRuntimeConfig } from "./ConfigProvider";

/** The live page is redrawn at most this often while it streams (I7). */
export const LIVE_REDRAW_MS = 300;

export type PanelTab = "preview" | "code";

const iconButton =
  "rounded-lg p-1.5 text-muted hover:bg-surface-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-focus disabled:opacity-40";

/** The latest value, at most once per `ms`; the last one always arrives. */
function useThrottled<T>(value: T, ms: number): T {
  const [shown, setShown] = useState(value);
  const last = useRef(0);
  useEffect(() => {
    const wait = last.current + ms - Date.now();
    const show = () => {
      last.current = Date.now();
      setShown(value);
    };
    if (wait <= 0) {
      show();
      return;
    }
    const timer = window.setTimeout(show, wait);
    return () => window.clearTimeout(timer);
  }, [value, ms]);
  return shown;
}

/** A panel call's failure: the envelope's own message in the page's language, never a raw code. */
function panelMessage(error: ErrorInfo, locale: Locale, translate: (key: `errors.${string}`) => string): string {
  const server = locale === "ar" ? error.message_ar : error.message_en;
  return server?.trim() ? server : resolveErrorMessage(error, locale, translate);
}

/** The card a reply that wrote a report shows in the chat: it reopens the panel at that version. */
export function ReportCard({ artifact, onOpen }: { artifact: ArtifactRef; onOpen?: (ref: ArtifactRef) => void }) {
  const t = useTranslations();
  return (
    <button
      type="button"
      onClick={() => onOpen?.(artifact)}
      aria-label={t("reports.open", { title: artifact.title })}
      data-report={artifact.id}
      data-report-version={artifact.version}
      className="mt-2 flex w-full max-w-sm items-center gap-3 rounded-xl px-3 py-2.5 text-start ring-1 ring-line hover:bg-surface-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-focus"
    >
      <span className="rounded-lg bg-accent-subtle p-2 text-accent-fg" aria-hidden>
        <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />
        </svg>
      </span>
      <span className="flex min-w-0 flex-col">
        <bdi dir="auto" className="truncate font-medium text-fg">
          {artifact.title}
        </bdi>
        <span className="text-xs text-muted">{t("reports.version", { version: String(artifact.version) })}</span>
      </span>
    </button>
  );
}

/** The preview frame: sandboxed with scripts only, never the parent's origin (I4). */
export function ReportFrame({ source, title }: { source: FrameSource; title: string }) {
  const t = useTranslations();
  if (source.kind === "loading") return <p className="p-6 text-center text-sm text-muted">{t("reports.loading")}</p>;
  return source.kind === "srcdoc" ? (
    <iframe title={title} sandbox={FRAME_SANDBOX} srcDoc={source.html} className="h-full w-full border-0 bg-surface" />
  ) : (
    // A fresh view link per load (I6); keyed so a new link reloads the frame.
    <iframe
      key={source.url}
      title={title}
      sandbox={FRAME_SANDBOX}
      src={source.url}
      referrerPolicy="no-referrer"
      className="h-full w-full border-0 bg-surface"
    />
  );
}

export type ReportPanelViewProps = {
  title: string;
  building: boolean;
  source: FrameSource;
  code: string | null;
  versions: ReportVersionSummary[];
  version: number | null;
  tab: PanelTab;
  error: ErrorInfo | null;
  copied: boolean;
  previewRef?: RefObject<HTMLDivElement | null>;
  onTab: (tab: PanelTab) => void;
  onSelect: (version: number) => void;
  onDownload: () => void;
  onFullscreen: () => void;
  onCopy: () => void;
  onClose: () => void;
};

/** The panel as it renders: header with the version switcher and buttons, tabs, preview and code. */
export function ReportPanelView(props: ReportPanelViewProps) {
  const { title, building, source, code, versions, version, tab, error, copied } = props;
  const t = useTranslations();
  const rawLocale = useLocale();
  const locale = isLocale(rawLocale) ? rawLocale : DEFAULT_LOCALE;
  const format = pageFormat(locale);
  const stored = !building && version !== null;
  const listed = versions.some((v) => v.version === version);

  function onKeyDown(e: KeyboardEvent<HTMLElement>) {
    if (e.key === "Escape") props.onClose();
  }

  return (
    <aside
      aria-label={t("reports.panel")}
      onKeyDown={onKeyDown}
      // Second in the row: on the left in Arabic, on the right in English; full width on a narrow screen.
      className="fixed inset-0 z-40 flex flex-col bg-surface md:static md:inset-auto md:z-auto md:h-full md:w-1/2 md:border-s md:border-line"
    >
      <header className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
        <h2 className="min-w-0 flex-1 truncate text-sm font-semibold text-fg">
          <bdi dir="auto">{title}</bdi>
        </h2>
        {building && (
          <span role="status" className="animate-pulse text-xs text-primary">
            {t("reports.building")}
          </span>
        )}
        {(versions.length > 0 || version !== null) && (
          <select
            aria-label={t("reports.versions")}
            value={version ?? ""}
            onChange={(e) => props.onSelect(Number(e.target.value))}
            disabled={building || versions.length < 2}
            className="rounded-lg border border-line-strong bg-surface px-2 py-1 text-xs text-fg focus:border-focus focus:outline-none disabled:opacity-60"
          >
            {version !== null && !listed && <option value={version}>{t("reports.version", { version: String(version) })}</option>}
            {versions.map((v) => (
              <option key={v.version} value={v.version}>
                {t("reports.versionDated", { version: String(v.version), date: format.time(v.created_at) })}
              </option>
            ))}
          </select>
        )}
        <button type="button" onClick={props.onDownload} disabled={!stored} aria-label={t("reports.download")} title={t("reports.download")} className={iconButton}>
          <svg viewBox="0 0 24 24" className="h-4 w-4" aria-hidden fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 4v11m0 0l-4-4m4 4l4-4M5 20h14" />
          </svg>
        </button>
        <button type="button" onClick={props.onFullscreen} aria-label={t("reports.fullscreen")} title={t("reports.fullscreen")} className={iconButton}>
          <svg viewBox="0 0 24 24" className="h-4 w-4" aria-hidden fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />
          </svg>
        </button>
        <button type="button" onClick={props.onCopy} disabled={!code} aria-label={t("reports.copy")} title={t("reports.copy")} className={iconButton}>
          <svg viewBox="0 0 24 24" className="h-4 w-4" aria-hidden fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M9 9h10v11H9zM5 15V4h10" />
          </svg>
        </button>
        {copied && (
          <span role="status" className="text-xs text-primary">
            {t("reports.copied")}
          </span>
        )}
        <button type="button" onClick={props.onClose} aria-label={t("reports.close")} title={t("reports.close")} className={iconButton}>
          <svg viewBox="0 0 24 24" className="h-4 w-4" aria-hidden fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>
      </header>

      <div role="tablist" aria-label={t("reports.tabs")} className="flex gap-1 border-b border-line px-3">
        {(["preview", "code"] as const).map((name) => (
          <button
            key={name}
            type="button"
            role="tab"
            id={`report-tab-${name}`}
            aria-selected={tab === name}
            aria-controls={`report-pane-${name}`}
            onClick={() => props.onTab(name)}
            className={`border-b-2 px-3 py-2 text-sm ${
              tab === name ? "border-focus font-medium text-primary" : "border-transparent text-muted hover:text-fg"
            }`}
          >
            {t(`reports.${name}`)}
          </button>
        ))}
      </div>

      {error && (
        <p role="alert" className="px-3 py-2 text-sm text-error">
          {panelMessage(error, locale, (key) => t(key))}
        </p>
      )}

      {/* The frame stays mounted behind the code tab, so a view link is not spent twice. */}
      <div
        ref={props.previewRef}
        id="report-pane-preview"
        role="tabpanel"
        aria-labelledby="report-tab-preview"
        hidden={tab !== "preview"}
        className="min-h-0 flex-1 bg-surface"
      >
        <ReportFrame source={source} title={title} />
      </div>
      <div id="report-pane-code" role="tabpanel" aria-labelledby="report-tab-code" hidden={tab !== "code"} className="min-h-0 flex-1 overflow-auto">
        {code === null ? (
          <p className="p-6 text-center text-sm text-muted">{t("reports.loading")}</p>
        ) : (
          <pre dir="ltr" tabIndex={0} className="whitespace-pre-wrap break-all p-3 text-start font-mono text-xs leading-5 text-fg">
            {code}
          </pre>
        )}
      </div>
    </aside>
  );
}

/**
 * The report side panel (reports PR, I7), like Claude's artifacts: the live page while it streams,
 * then the stored version through a short-lived view link; its code; download, full screen, copy and
 * the version switcher. Controlled by the chat's report state; it loads what it shows itself.
 */
export function ReportPanel({ state, onAction }: { state: ReportPanelState; onAction: (action: ReportAction) => void }) {
  const config = useRuntimeConfig();
  const auth = useAuth();
  const kit = kitUrl(config.gatewayUrl);
  const [tab, setTab] = useState<PanelTab>("preview");
  // Kept with the report's id: another report's versions are never listed.
  const [versions, setVersions] = useState<{ id: string; list: ReportVersionSummary[] } | null>(null);
  const [stored, setStored] = useState<{ key: string; html: string } | null>(null);
  const [view, setView] = useState<{ key: string; url: string } | null>(null);
  const [error, setError] = useState<ErrorInfo | null>(null);
  const [copied, setCopied] = useState(false);
  const previewRef = useRef<HTMLDivElement>(null);

  const building = state.live !== null;
  const liveHtml = useThrottled(state.live?.html ?? "", LIVE_REDRAW_MS);
  const key = state.id && state.version !== null ? `${state.id}@${state.version}` : null;
  const showing = !building && state.open ? key : null;

  // The stored version: its page for the code tab and a fresh view link for the frame (I6).
  useEffect(() => {
    if (!showing || !state.id || state.version === null) return;
    const controller = new AbortController();
    const [id, version] = [state.id, state.version];
    setError(null);
    void (async () => {
      const token = await auth.getAccessToken();
      if (!token) return;
      const call = { gatewayUrl: config.gatewayUrl, token, id, version, signal: controller.signal };
      try {
        const [page, link] = await Promise.all([getReportVersion(call), createReportView(call)]);
        setStored({ key: showing, html: page.html });
        setView({ key: showing, url: link.url });
      } catch (failure) {
        if (!isAbortError(failure)) setError(failure instanceof GatewayError ? failure.info : { code: "unknown" });
      }
    })();
    return () => controller.abort();
  }, [showing, state.id, state.version, auth, config.gatewayUrl]);

  // The report's versions for the switcher, again when a new one is stored.
  const list = versions && versions.id === state.id ? versions.list : [];
  const listed = list.some((v) => v.version === state.version);
  useEffect(() => {
    if (!state.open || !state.id || building || listed) return;
    const controller = new AbortController();
    const id = state.id;
    void (async () => {
      const token = await auth.getAccessToken();
      if (!token) return;
      try {
        const report = await getReport({ gatewayUrl: config.gatewayUrl, token, id, signal: controller.signal });
        setVersions({ id, list: newestFirst(report.versions) });
      } catch (failure) {
        if (!isAbortError(failure)) setError(failure instanceof GatewayError ? failure.info : { code: "unknown" });
      }
    })();
    return () => controller.abort();
  }, [state.open, state.id, state.version, building, listed, auth, config.gatewayUrl]);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 2000);
    return () => window.clearTimeout(timer);
  }, [copied]);

  if (!state.open) return null;

  const viewUrl = view && view.key === key ? view.url : null;
  const source = frameSource(state.live ? { ...state, live: { ...state.live, html: liveHtml } } : state, viewUrl, kit);
  const code = building ? liveHtml : stored && stored.key === key ? stored.html : null;

  async function onDownload() {
    if (!state.id || state.version === null) return;
    const [id, version] = [state.id, state.version];
    const token = await auth.getAccessToken();
    if (!token) return;
    try {
      const blob = await downloadReport({ gatewayUrl: config.gatewayUrl, token, id, version });
      const href = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = href;
      link.download = reportFileName(id, version);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(href), 1000);
    } catch (failure) {
      setError(failure instanceof GatewayError ? failure.info : { code: "unknown" });
    }
  }

  function onFullscreen() {
    const pane = previewRef.current;
    // A browser without the Fullscreen API keeps the panel as it is.
    if (!pane || typeof pane.requestFullscreen !== "function" || document.fullscreenEnabled === false) return;
    setTab("preview");
    pane.requestFullscreen().catch(() => {});
  }

  function onCopy() {
    if (!code || !navigator.clipboard) return;
    navigator.clipboard.writeText(code).then(
      () => setCopied(true),
      () => {},
    );
  }

  return (
    <ReportPanelView
      title={state.title}
      building={building}
      source={source}
      code={code}
      versions={list}
      version={state.version ?? state.live?.version ?? null}
      tab={tab}
      error={error}
      copied={copied}
      previewRef={previewRef}
      onTab={setTab}
      onSelect={(version) => onAction({ type: "select", version })}
      onDownload={() => void onDownload()}
      onFullscreen={onFullscreen}
      onCopy={onCopy}
      onClose={() => onAction({ type: "close" })}
    />
  );
}
