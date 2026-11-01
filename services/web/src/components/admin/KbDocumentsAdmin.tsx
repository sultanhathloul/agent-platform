"use client";

import { useLocale, useTranslations } from "next-intl";
import Link from "next/link";
import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import type { AttachmentLimits, KbDocumentView, KbView } from "@/lib/api/types";
import { POLL_INTERVAL_MS, getLimits } from "@/lib/attachments";
import { resolveErrorMessage, type ErrorInfo } from "@/lib/errors";
import { GatewayError, getKb, listKbDocuments, reindexKbDocument, updateKbMetadata, uploadKbDocument } from "@/lib/gateway";
import {
  EMPTY_METADATA,
  type MetadataDraft,
  type MetadataProblem,
  draftOf,
  labelIn,
  metadataProblems,
  supersedable,
  toMetadata,
  versionLabel,
} from "@/lib/kbs";
import { DEFAULT_LOCALE, isLocale } from "@/lib/locale";
import { pageFormat } from "@/lib/settings";
import { useAuth } from "../AuthProvider";
import { useRuntimeConfig } from "../ConfigProvider";
import { Chip, button, card, quiet } from "./SettingsParts";

const field = "w-full rounded-md border border-line-strong px-2.5 py-1.5 text-sm focus:border-focus focus:outline-none";
const badge: Record<string, string> = {
  pending: "bg-warning-bg text-warning ring-warning-line",
  indexed: "bg-success-bg text-primary ring-success-line",
  failed: "bg-error-bg text-error ring-error-line",
};

/** The D3 fields, for an upload and for a correction; the supersedes picker offers only valid choices. */
export function MetadataFields({
  draft,
  onChange,
  problems,
  choices,
}: {
  draft: MetadataDraft;
  onChange: (draft: MetadataDraft) => void;
  problems: MetadataProblem[];
  choices: KbDocumentView[];
}) {
  const t = useTranslations("kbs");
  const shown = (name: MetadataProblem) => problems.includes(name);
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <label className="block text-sm">
        <span className="text-fg">{t("docTitle")}</span>
        <input dir="auto" className={field} value={draft.title} maxLength={300} aria-invalid={shown("title")}
          onChange={(e) => onChange({ ...draft, title: e.target.value })} />
      </label>
      <label className="block text-sm">
        <span className="text-fg">{t("publisher")}</span>
        <input dir="auto" className={field} value={draft.publisher} maxLength={200} aria-invalid={shown("publisher")}
          onChange={(e) => onChange({ ...draft, publisher: e.target.value })} />
      </label>
      <label className="block text-sm">
        <span className="text-fg">{t("publishedOn")}</span>
        <input type="date" dir="ltr" lang="en" className={field} value={draft.published_on} aria-invalid={shown("published_on")}
          onChange={(e) => onChange({ ...draft, published_on: e.target.value })} />
        <span className="mt-0.5 block text-xs text-muted">{t("publishedOnHint")}</span>
      </label>
      <label className="block text-sm">
        <span className="text-fg">{t("sourceUrl")}</span>
        <input dir="ltr" className={field} value={draft.source_url} maxLength={2000} aria-invalid={shown("source_url")}
          placeholder="https://" onChange={(e) => onChange({ ...draft, source_url: e.target.value })} />
        <span className={`mt-0.5 block text-xs ${shown("source_url") ? "text-error" : "text-muted"}`}>{t("sourceUrlHint")}</span>
      </label>
      <label className="block text-sm sm:col-span-2">
        <span className="text-fg">{t("supersedes")}</span>
        <select className={field} value={draft.supersedes} onChange={(e) => onChange({ ...draft, supersedes: e.target.value })}>
          <option value="">{t("supersedesNone")}</option>
          {choices.map((d) => (
            <option key={d.id} value={d.id}>
              {versionLabel(d)}
            </option>
          ))}
        </select>
        <span className="mt-0.5 block text-xs text-muted">{t("supersedesHint")}</span>
      </label>
    </div>
  );
}

/**
 * /admin/kbs/<slug> (phase 08, I5): upload a document with its source metadata (D3), each document's
 * status badge, its earlier and later versions, and a form to correct its metadata. Pending documents
 * are read again every 2 s. Arabic first, Western digits (ADR-0288), no code or JSON to type.
 */
/** A re-index beside the indexed chunks (ADR-0424): the badge, or (with `onRetry`) the line under the title,
 * which for a failed re-index says the document is still searchable, why it failed, and offers a retry. */
export function ReindexState({ document, onRetry }: { document: KbDocumentView; onRetry?: () => void }) {
  const t = useTranslations("kbs");
  const tr = useTranslations();
  const state = document.reindex_status;
  if (!state) return null;
  if (!onRetry) {
    const tone = state === "reindexing" ? badge.pending : badge.failed;
    return (
      <span className={`rounded-full px-2 py-0.5 text-xs ring-1 ${tone}`} data-reindex={state}>
        {t(state === "reindexing" ? "reindexing" : "reindexFailed")}
      </span>
    );
  }
  if (state === "reindexing") return <p className="mt-1 text-xs text-muted">{t("reindexingHint")}</p>;
  return (
    <div className="mt-1 flex flex-wrap items-center gap-2">
      <p className="text-xs text-error">
        {t("reindexFailedHint", {
          reason: document.reindex_reason ? tr(`attachments.reasons.${document.reindex_reason}`) : "—",
        })}
      </p>
      <button type="button" className={quiet} onClick={onRetry}>
        {t("reindexRetry")}
      </button>
    </div>
  );
}

/** A seeded file uploaded without its sidecar (ADR-0392): the badge, or the line saying what to correct. */
export function MetadataMissing({ hint = false }: { hint?: boolean }) {
  const t = useTranslations("kbs");
  return hint ? (
    <p className="mt-1 text-xs text-warning">{t("metadataMissingHint")}</p>
  ) : (
    <span className="rounded-full bg-warning-bg px-2 py-0.5 text-xs text-warning ring-1 ring-warning-line" data-metadata-missing>
      {t("metadataMissing")}
    </span>
  );
}

export function KbDocumentsAdmin({ slug }: { slug: string }) {
  const t = useTranslations("kbs");
  const tr = useTranslations();
  const rawLocale = useLocale();
  const locale = isLocale(rawLocale) ? rawLocale : DEFAULT_LOCALE;
  const format = pageFormat(locale);
  const auth = useAuth();
  const config = useRuntimeConfig();
  const [kb, setKb] = useState<KbView | null>(null);
  const [documents, setDocuments] = useState<KbDocumentView[] | null>(null);
  const [limits, setLimits] = useState<AttachmentLimits | null>(null);
  const [draft, setDraft] = useState<MetadataDraft>(EMPTY_METADATA);
  const [file, setFile] = useState<File | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<ErrorInfo | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [tried, setTried] = useState(false);
  const [editing, setEditing] = useState<{ id: string; draft: MetadataDraft } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const message = (info: ErrorInfo) => resolveErrorMessage(info, locale, (key) => tr(key));
  const failure = (e: unknown): ErrorInfo => (e instanceof GatewayError ? e.info : { code: "unknown" });

  const load = useCallback(async () => {
    const token = await auth.getAccessToken();
    if (!token) return;
    try {
      const [loadedKb, loaded] = await Promise.all([
        getKb({ gatewayUrl: config.gatewayUrl, token, slug }),
        listKbDocuments({ gatewayUrl: config.gatewayUrl, token, slug }),
      ]);
      setKb(loadedKb);
      setDocuments(loaded.documents);
    } catch (e) {
      setError(failure(e));
    }
  }, [auth, config.gatewayUrl, slug]);

  useEffect(() => {
    void load();
    void (async () => {
      const token = await auth.getAccessToken();
      if (token) setLimits(await getLimits({ gatewayUrl: config.gatewayUrl, token }).catch(() => null));
    })();
  }, [load, auth, config.gatewayUrl]);

  const pending = (documents ?? []).some((d) => d.status === "pending" || d.reindex_status === "reindexing");
  useEffect(() => {
    if (!pending) return;
    const timer = window.setInterval(() => void load(), POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [pending, load]);

  const problems = metadataProblems(draft);
  const byId = new Map((documents ?? []).map((d) => [d.id, d]));

  async function onUpload(e: FormEvent) {
    e.preventDefault();
    setTried(true);
    if (!file || problems.length > 0 || progress !== null) return;
    setError(null);
    setNotice(null);
    setProgress(0);
    try {
      const token = await auth.getAccessToken();
      if (!token) return;
      const uploaded = await uploadKbDocument({
        gatewayUrl: config.gatewayUrl,
        token,
        slug,
        file,
        metadata: toMetadata(draft),
        onProgress: setProgress,
      });
      setNotice(t("uploaded", { title: uploaded.metadata.title }));
      setDraft(EMPTY_METADATA);
      setFile(null);
      setTried(false);
      if (fileRef.current) fileRef.current.value = "";
      await load();
    } catch (err) {
      setError(failure(err));
    } finally {
      setProgress(null);
    }
  }

  async function onReindex(d: KbDocumentView) {
    setError(null);
    setNotice(null);
    try {
      const token = await auth.getAccessToken();
      if (!token) return;
      await reindexKbDocument({ gatewayUrl: config.gatewayUrl, token, slug, id: d.id });
      setNotice(t("reindexStarted", { title: d.metadata.title }));
      await load();
    } catch (err) {
      setError(failure(err));
    }
  }

  async function onSaveMetadata(e: FormEvent) {
    e.preventDefault();
    if (!editing || metadataProblems(editing.draft).length > 0) return;
    setError(null);
    setNotice(null);
    try {
      const token = await auth.getAccessToken();
      if (!token) return;
      const saved = await updateKbMetadata({
        gatewayUrl: config.gatewayUrl,
        token,
        slug,
        id: editing.id,
        metadata: toMetadata(editing.draft),
      });
      setNotice(t("metadataSaved", { title: saved.metadata.title }));
      setEditing(null);
      await load();
    } catch (err) {
      setError(failure(err));
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-4">
      <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <Link href="/admin/kbs" className="text-sm text-primary hover:underline">
          {t("back")}
        </Link>
        <h1 className="text-xl font-semibold">
          <bdi dir="auto">{kb ? labelIn(kb.name, locale) : slug}</bdi>
        </h1>
        {kb && (
          <span className="flex items-center gap-1.5 text-xs text-muted">
            {t("role")} <Chip name="role">{kb.role}</Chip>
          </span>
        )}
      </header>

      {error && (
        <p role="alert" className="rounded-md bg-error-bg px-3 py-2 text-sm text-error">
          {message(error)}
        </p>
      )}
      {notice && (
        <p role="status" className="rounded-md bg-success-bg px-3 py-2 text-sm text-success">
          {notice}
        </p>
      )}

      <form onSubmit={onUpload} className={card} aria-labelledby="kb-upload">
        <h2 id="kb-upload" className="mb-1 text-base font-semibold">
          {t("uploadTitle")}
        </h2>
        <p className="mb-3 text-xs text-muted">{t("uploadHint")}</p>
        <label className="mb-3 block text-sm">
          <span className="text-fg">{t("file")}</span>
          <input
            ref={fileRef}
            type="file"
            className="mt-1 block text-sm"
            accept={limits ? limits.extensions.map((x) => `.${x}`).join(",") : undefined}
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
          {limits && (
            <span className="mt-0.5 block text-xs text-muted">
              {t("fileLimits", {
                size: format.number(Math.round(limits.max_file_bytes / (1024 * 1024))),
                types: limits.extensions.join("، "),
              })}
            </span>
          )}
          {tried && !file && <span className="mt-0.5 block text-xs text-error">{t("fileMissing")}</span>}
        </label>
        <MetadataFields draft={draft} onChange={setDraft} problems={tried ? problems : []}
          choices={supersedable(documents ?? [], null)} />
        {tried && problems.length > 0 && <p className="mt-2 text-xs text-error">{t("metadataProblems")}</p>}
        <div className="mt-3 flex items-center gap-3">
          <button type="submit" className={button} disabled={progress !== null}>
            {progress !== null ? t("uploading", { percent: format.number(progress) }) : t("upload")}
          </button>
        </div>
      </form>

      <section className={card} aria-labelledby="kb-documents">
        <h2 id="kb-documents" className="mb-3 text-base font-semibold">
          {t("documentsTitle")}
        </h2>
        {documents === null ? (
          <p className="text-sm text-muted">{t("loading")}</p>
        ) : documents.length === 0 ? (
          <p className="text-sm text-muted">{t("noDocuments")}</p>
        ) : (
          <ul className="divide-y divide-line">
            {documents.map((d) => {
              const earlier = d.metadata.supersedes ? byId.get(d.metadata.supersedes) : undefined;
              const later = d.superseded_by ? byId.get(d.superseded_by) : undefined;
              return (
                <li key={d.id} className="py-3" data-document={d.id}>
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                    <bdi dir="auto" className="font-medium text-fg">
                      {d.metadata.title}
                    </bdi>
                    <span className={`rounded-full px-2 py-0.5 text-xs ring-1 ${badge[d.status] ?? ""}`} data-status={d.status}>
                      {tr(`attachments.status.${d.status}`)}
                    </span>
                    <ReindexState document={d} />
                    {later && <span className="rounded-full bg-surface-2 px-2 py-0.5 text-xs text-muted">{t("superseded")}</span>}
                    {d.metadata_missing && <MetadataMissing />}
                  </div>
                  <ReindexState document={d} onRetry={() => void onReindex(d)} />
                  {d.metadata_missing && <MetadataMissing hint />}
                  <p className="mt-1 text-xs text-muted">
                    <bdi dir="auto">{d.metadata.publisher}</bdi>
                    {" · "}
                    <span dir="ltr">{d.metadata.published_on}</span>
                    {" · "}
                    <bdi dir="auto">{d.name}</bdi>
                    {d.pages != null && <> {" · "}{t("pages", { pages: format.number(d.pages) })}</>}
                    {d.metadata.source_url && (
                      <>
                        {" · "}
                        <a href={d.metadata.source_url} target="_blank" rel="noopener noreferrer" className="text-info underline">
                          {t("sourceLink")}
                        </a>
                      </>
                    )}
                  </p>
                  {d.status === "failed" && d.reason && (
                    <p className="mt-1 text-xs text-error">{tr(`attachments.reasons.${d.reason}`)}</p>
                  )}
                  {earlier && <p className="mt-1 text-xs text-muted">{t("supersedesLine", { title: earlier.metadata.title })}</p>}
                  {later && <p className="mt-1 text-xs text-muted">{t("supersededLine", { title: later.metadata.title })}</p>}
                  {editing?.id === d.id ? (
                    <form onSubmit={onSaveMetadata} className="mt-3 rounded-xl bg-surface-2 p-3">
                      <MetadataFields draft={editing.draft} onChange={(next) => setEditing({ id: d.id, draft: next })}
                        problems={metadataProblems(editing.draft)} choices={supersedable(documents, d.id)} />
                      <div className="mt-3 flex gap-2">
                        <button type="submit" className={button} disabled={metadataProblems(editing.draft).length > 0}>
                          {t("save")}
                        </button>
                        <button type="button" className={quiet} onClick={() => setEditing(null)}>
                          {t("cancel")}
                        </button>
                      </div>
                    </form>
                  ) : (
                    <button type="button" className={`${quiet} mt-2`} onClick={() => setEditing({ id: d.id, draft: draftOf(d) })}>
                      {t("editMetadata")}
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
