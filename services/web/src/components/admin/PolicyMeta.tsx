"use client";

import { useLocale, useTranslations } from "next-intl";
import type { PolicyDocument } from "@/lib/api/types";
import { formatList } from "@/lib/policies";

/** `id@version`, how policies are named in events, traces and the audit log (A1). */
export function policyRef(id: string, version: number | null | undefined): string {
  return [id, version ?? 1].join("@");
}

const chip = "rounded-full bg-slate-100 px-1.5 text-[11px] text-slate-600";
// Latin identifiers only: a monospace face never holds Arabic text (shaping, ADR-0119).
const code = `${chip} font-mono`;

/**
 * The meta line of a policy (ADR-0119): one chip per item, each in its own <bdi>, in a fixed order
 * in both locales: action, the exception badge when there is one, scope, checkpoints (joined with a
 * Latin comma in one chip), checker, id@version. No separators between chips.
 */
export function PolicyMeta({
  policy,
  exception = false,
  overrides = [],
}: {
  policy: Pick<PolicyDocument, "id" | "version" | "action" | "scope" | "checkpoints" | "checker">;
  exception?: boolean;
  overrides?: string[];
}) {
  const t = useTranslations();
  const locale = useLocale();
  const actionKey = `guardActions.${policy.action}`;
  return (
    <span className="flex flex-wrap gap-1" data-policy-meta="">
      <bdi data-chip="action" className={chip}>
        {t.has(actionKey) ? t(actionKey) : policy.action}
      </bdi>
      {exception && (
        <bdi data-chip="exception" className="rounded-full bg-amber-100 px-1.5 text-[11px] text-amber-900" title={formatList(overrides, locale)}>
          {t("policies.overrides.badge")}
        </bdi>
      )}
      <bdi data-chip="scope" dir="ltr" className={code}>
        {policy.scope}
      </bdi>
      <bdi data-chip="checkpoints" dir="ltr" className={code}>
        {policy.checkpoints.join(", ")}
      </bdi>
      <bdi data-chip="checker" dir="ltr" className={code}>
        {policy.checker}
      </bdi>
      <bdi data-chip="ref" dir="ltr" className={code}>
        {policyRef(policy.id, policy.version)}
      </bdi>
    </span>
  );
}
