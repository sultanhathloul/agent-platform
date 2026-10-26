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
    <div className="flex flex-wrap items-center gap-1.5 text-xs text-slate-600" role="group" aria-label={t("chat.kbToggles")}>
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
              on ? "bg-emerald-700 text-white ring-emerald-700" : "bg-white text-slate-700 ring-slate-300 hover:bg-slate-50"
            }`}
          >
            <bdi dir="auto">{labelIn(kb.name, locale)}</bdi>
          </button>
        );
      })}
    </div>
  );
}
