"use client";

import { useLocale, useTranslations } from "next-intl";
import type { SkillView } from "@/lib/api/types";
import { DEFAULT_LOCALE, isLocale } from "@/lib/locale";
import { NO_SKILL, skillName } from "@/lib/skills";

/**
 * The composer's skill picker (phase 06, ADR-0292): only the caller's skills, from GET /v1/skills;
 * "automatic" by default; no confirmation step. Hidden when the caller has no skill. The names come
 * from each SKILL.md, so a new skill needs no change here.
 */
export function SkillPicker({
  skills,
  value,
  onChange,
  disabled = false,
  disabledHint,
}: {
  skills: SkillView[];
  value: string;
  onChange: (slug: string) => void;
  disabled?: boolean;
  /** Why the picker is off, as its tooltip (a report applies no skill, D1). */
  disabledHint?: string;
}) {
  const t = useTranslations();
  const rawLocale = useLocale();
  const locale = isLocale(rawLocale) ? rawLocale : DEFAULT_LOCALE;
  if (skills.length === 0) return null;
  const chosen = skills.find((s) => s.slug === value);
  return (
    <div className="flex items-center gap-2 text-xs text-slate-600">
      <label htmlFor="skill-picker">{t("chat.skillPicker")}</label>
      <select
        id="skill-picker"
        value={chosen ? value : NO_SKILL}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        title={disabledHint ?? (chosen ? skillName(chosen.description, locale) : t("chat.skillAutoHint"))}
        className="rounded-lg border border-slate-300 bg-white px-2 py-1 text-xs text-slate-700 focus:border-emerald-600 focus:outline-none disabled:opacity-40"
      >
        <option value={NO_SKILL}>{t("chat.skillAuto")}</option>
        {skills.map((s) => (
          <option key={s.slug} value={s.slug} title={skillName(s.description, locale)}>
            {skillName(s.name, locale)}
          </option>
        ))}
      </select>
    </div>
  );
}
