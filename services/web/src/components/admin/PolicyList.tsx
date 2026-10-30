"use client";

import { useLocale, useTranslations } from "next-intl";
import type { PolicyView } from "@/lib/api/types";
import { policyName, visiblePolicies } from "@/lib/policies";
import { PolicyMeta } from "./PolicyMeta";

/**
 * The admin's policy list: each policy's name in the reader's locale and its meta line. Test
 * policies (smoke, TTFT) stay hidden until "Show test policies" is on (ADR-0119); the page owns
 * the toggle, so the daily matches table follows it too (ADR-0123). A name cut short by the
 * column shows in full in a tooltip.
 */
export function PolicyList({
  policies,
  selected,
  busy,
  showTest,
  onShowTest,
  onOpen,
  onToggle,
}: {
  policies: PolicyView[];
  selected: string | null;
  busy: string | null;
  showTest: boolean;
  onShowTest: (show: boolean) => void;
  onOpen: (id: string) => void;
  onToggle: (view: PolicyView) => void;
}) {
  const t = useTranslations();
  const locale = useLocale();
  const shown = visiblePolicies(policies, showTest);

  return (
    <>
      <label className="mb-2 flex items-center gap-1.5 text-xs text-muted">
        <input type="checkbox" checked={showTest} onChange={(e) => onShowTest(e.target.checked)} />
        {t("policies.showTest")}
      </label>
      {shown.length === 0 ? (
        <p className="text-sm text-muted">{t("policies.empty")}</p>
      ) : (
        <ul className="divide-y divide-line">
          {shown.map((view) => {
            const p = view.policy;
            const on = p.enabled ?? true;
            const name = policyName(p, locale);
            return (
              <li key={p.id} className="flex items-start justify-between gap-3 py-2" data-policy-id={p.id}>
                <button
                  type="button"
                  onClick={() => onOpen(p.id)}
                  className={`min-w-0 flex-1 text-start ${selected === p.id ? "text-primary" : "text-fg"}`}
                >
                  <span dir="auto" className="block truncate font-medium" title={name}>
                    {name}
                  </span>
                  <span className="mt-0.5 block">
                    <PolicyMeta policy={p} exception={Boolean(view.exception)} overrides={view.overrides ?? []} />
                  </span>
                </button>
                <label className="flex shrink-0 items-center gap-1.5 text-xs text-muted">
                  <input
                    type="checkbox"
                    checked={on}
                    disabled={busy === `toggle:${p.id}`}
                    onChange={() => onToggle(view)}
                    aria-label={t("policies.toggleLabel", { name })}
                  />
                  {on ? t("policies.enabled") : t("policies.disabled")}
                </label>
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}
