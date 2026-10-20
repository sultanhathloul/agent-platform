"use client";

import { useLocale, useTranslations } from "next-intl";
import type { AttachmentLimits } from "@/lib/api/types";
import { type ComposerFile, isDismissible, pagesWithoutText, reasonKey, sizeInMb } from "@/lib/attachments";
import { resolveErrorMessage } from "@/lib/errors";
import { DEFAULT_LOCALE, isLocale } from "@/lib/locale";

const badgeClass: Record<string, string> = {
  pending: "bg-amber-50 text-amber-800 ring-amber-200",
  indexed: "bg-emerald-50 text-emerald-800 ring-emerald-200",
  failed: "bg-red-50 text-red-800 ring-red-200",
};

/** The limits line shown before a file is chosen (D2): numbers from the gateway, words from i18n. */
export function LimitsHint({ limits }: { limits: AttachmentLimits }) {
  const t = useTranslations("attachments");
  const rawLocale = useLocale();
  const locale = isLocale(rawLocale) ? rawLocale : DEFAULT_LOCALE;
  const kinds = Array.from(new Set(limits.extensions.map((e) => (e === "markdown" ? "md" : e))));
  const names = kinds.filter((k) => t.has(`types.${k}`)).map((k) => t(`types.${k}`));
  const list = new Intl.ListFormat(locale === "ar" ? "ar" : "en", { type: "conjunction" }).format(names);
  const number = new Intl.NumberFormat(locale === "ar" ? "ar" : "en");
  return (
    <p className="text-xs text-slate-500">
      {t("limits", {
        files: number.format(limits.max_files_per_conversation),
        size: number.format(sizeInMb(limits.max_file_bytes)),
        pages: number.format(limits.max_pdf_pages),
        types: list,
      })}
    </p>
  );
}

/** One file in the composer: its name, its upload progress or status badge, and why it failed. */
export function AttachmentChip({ file, onRemove }: { file: ComposerFile; onRemove?: () => void }) {
  const t = useTranslations();
  const rawLocale = useLocale();
  const locale = isLocale(rawLocale) ? rawLocale : DEFAULT_LOCALE;
  const status = file.error ? "failed" : file.attachment?.status;
  const number = new Intl.NumberFormat(locale === "ar" ? "ar" : "en");
  const percent = number.format(file.progress);
  // An indexed PDF with pages it could not read says how many (D7, ADR-0201).
  const unread = status === "indexed" ? pagesWithoutText(file.attachment) : 0;
  let detail: string | null = null;
  if (file.error) {
    detail = resolveErrorMessage(file.error, locale, (key) => t(key));
  } else if (status === "failed") {
    detail = t(reasonKey(file.attachment?.reason, (key) => t.has(key)));
  }
  return (
    <li className="flex max-w-full flex-col gap-0.5 rounded-lg bg-slate-50 px-2.5 py-1.5 text-sm ring-1 ring-slate-200">
      <div className="flex items-center gap-2">
        <bdi dir="auto" className="truncate font-medium text-slate-800" title={file.name}>
          {file.name}
        </bdi>
        {status ? (
          <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] ring-1 ${badgeClass[status] ?? badgeClass.pending}`}>
            {unread > 0
              ? t("attachments.indexedWithUnreadPages", { count: unread, shown: number.format(unread) })
              : t(`attachments.status.${status}`)}
          </span>
        ) : (
          <span className="shrink-0 text-[11px] text-slate-500">{t("attachments.uploading", { percent })}</span>
        )}
        {onRemove && (
          <button
            type="button"
            onClick={onRemove}
            aria-label={t("attachments.remove")}
            title={t("attachments.remove")}
            className="ms-auto shrink-0 rounded px-1 text-slate-400 hover:bg-slate-200 hover:text-slate-700"
          >
            <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" aria-hidden fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M4 4l8 8M12 4l-8 8" />
            </svg>
          </button>
        )}
      </div>
      {!status && (
        <div className="h-1 w-full overflow-hidden rounded bg-slate-200" aria-hidden>
          <div className="h-full bg-emerald-600 transition-all" style={{ width: `${file.progress}%` }} />
        </div>
      )}
      {detail && (
        <p role="alert" className="text-xs text-red-700">
          {detail}
        </p>
      )}
      {unread > 0 && <p className="text-xs text-amber-800">{t("attachments.unreadPagesNote")}</p>}
    </li>
  );
}

export function AttachmentTray({ files, onRemove }: { files: ComposerFile[]; onRemove: (localId: string) => void }) {
  const t = useTranslations("attachments");
  if (files.length === 0) return null;
  return (
    <ul aria-label={t("listLabel")} className="mb-2 flex flex-wrap gap-2">
      {files.map((file) => (
        <AttachmentChip
          key={file.localId}
          file={file}
          onRemove={isDismissible(file) ? () => onRemove(file.localId) : undefined}
        />
      ))}
    </ul>
  );
}
