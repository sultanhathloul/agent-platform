"use client";

import { useLocale, useTranslations } from "next-intl";
import type { AttachmentLimits } from "@/lib/api/types";
import { type ComposerFile, isDismissible, pagesWithoutText, reasonKey, sizeInMb } from "@/lib/attachments";
import { resolveErrorMessage } from "@/lib/errors";
import { DEFAULT_LOCALE, isLocale } from "@/lib/locale";

const badgeClass: Record<string, string> = {
  pending: "bg-warning-bg text-warning ring-warning-line",
  indexed: "bg-success-bg text-primary ring-success-line",
  failed: "bg-error-bg text-error ring-error-line",
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
    <p className="text-xs text-muted">
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
    <li className="flex max-w-full flex-col gap-0.5 rounded-lg bg-surface-2 px-2.5 py-1.5 text-sm ring-1 ring-line">
      <div className="flex items-center gap-2">
        <bdi dir="auto" className="truncate font-medium text-fg" title={file.name}>
          {file.name}
        </bdi>
        {status ? (
          <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] ring-1 ${badgeClass[status] ?? badgeClass.pending}`}>
            {unread > 0
              ? t("attachments.indexedWithUnreadPages", { count: unread, shown: number.format(unread) })
              : t(`attachments.status.${status}`)}
          </span>
        ) : (
          <span className="shrink-0 text-[11px] text-muted">{t("attachments.uploading", { percent })}</span>
        )}
        {onRemove && (
          <button
            type="button"
            onClick={onRemove}
            aria-label={t("attachments.remove")}
            title={t("attachments.remove")}
            className="ms-auto shrink-0 rounded px-1 text-subtle hover:bg-line hover:text-fg"
          >
            <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" aria-hidden fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M4 4l8 8M12 4l-8 8" />
            </svg>
          </button>
        )}
      </div>
      {!status && (
        <div className="h-1 w-full overflow-hidden rounded bg-line" aria-hidden>
          <div className="h-full bg-primary transition-all" style={{ width: `${file.progress}%` }} />
        </div>
      )}
      {detail && (
        <p role="alert" className="text-xs text-error">
          {detail}
        </p>
      )}
      {unread > 0 && <p className="text-xs text-warning">{t("attachments.unreadPagesNote")}</p>}
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
