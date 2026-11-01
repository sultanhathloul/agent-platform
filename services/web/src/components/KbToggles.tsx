"use client";

import { useLocale, useTranslations } from "next-intl";
import type { KbToggle } from "@/lib/api/types";
import { DEFAULT_LOCALE, isLocale } from "@/lib/locale";
import { labelIn } from "@/lib/kbs";

/**
 * The composer's knowledge base toggles (phase 08, I3), rendered from GET /v1/capabilities: only the
 * KBs the caller may read, none on by default. With none on, the kb capability is not used. The
 * names come from each KB's record, so a new KB needs no change here.
 */
export function KbToggles({
  kbs,
  selected,
  onToggle,
  disabled = false,
}: {
  kbs: KbToggle[];
  selected: string[];
  onToggle: (slug: string) => void;
  disabled?: boolean;
}) {
  const t = useTranslations();
  const rawLocale = useLocale();
  const locale = isLocale(rawLocale) ? rawLocale : DEFAULT_LOCALE;
  if (kbs.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted" role="group" aria-label={t("chat.kbToggles")}>
      <span>{t("chat.kbToggles")}</span>
      {kbs.map((kb) => {
        const on = selected.includes(kb.slug);
        const hint = labelIn(kb.description, locale) || t("chat.kbToggleHint");
        return (
          <button
            key={kb.slug}
            type="button"
            aria-pressed={on}
            data-kb={kb.slug}
            onClick={() => onToggle(kb.slug)}
            disabled={disabled}
            title={hint}
            className={`rounded-full px-2.5 py-1 ring-1 disabled:opacity-40 ${
              on ? "bg-accent-subtle font-medium text-fg ring-2 ring-focus" : "bg-surface text-fg ring-line-strong hover:bg-surface-2"
            }`}
          >
            <bdi dir="auto">{labelIn(kb.name, locale)}</bdi>
          </button>
        );
      })}
    </div>
  );
}
