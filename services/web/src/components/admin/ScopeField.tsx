"use client";

import { useTranslations } from "next-intl";
import { SCOPE_KINDS, parseScope, scopeText, scopeValueFor, unknownPolicyRole, type ScopeKind } from "@/lib/policies";

const input = "w-full rounded-md border border-line-strong px-2 py-1.5 text-sm";
const chip = "rounded-full bg-surface-2 px-1.5 font-mono text-[11px] text-muted";

// Example ids are identifiers, the same in both locales: each is its own chip after the sentence.
const SCOPE_EXAMPLES: Record<ScopeKind, string[]> = {
  global: [],
  tenant: ["default"],
  role: [],
  kb: ["moe"],
  tool: ["web", "sql"],
};
export const GROUP_EXAMPLES = ["pii-output", "pii-memory"];

/** A short sentence of the locale, then each example id in its own <bdi> chip (ADR-0123). */
export function Hint({ text, examples }: { text: string; examples: string[] }) {
  const t = useTranslations();
  return (
    <span className="flex flex-wrap items-center gap-1 text-[11px] text-muted" data-hint="">
      <span>{text}</span>
      {examples.length > 0 && <span>{t("policies.fields.example")}</span>}
      {examples.map((example) => (
        <bdi key={example} dir="ltr" className={chip} data-example="">
          {example}
        </bdi>
      ))}
    </span>
  );
}

/**
 * The scope as two fields (ADR-0123): a kind and a value. Global has no value; a role is chosen
 * from the gateway's policy:* roles (ADR-0107); a tenant starts as the default tenant.
 */
export function ScopeField({
  scope,
  realmRoles,
  onChange,
}: {
  scope: string;
  realmRoles: string[] | null;
  onChange: (scope: string) => void;
}) {
  const t = useTranslations();
  const { kind, value } = parseScope(scope);
  const missingRole = unknownPolicyRole(scope, realmRoles);
  const roles = (realmRoles ?? []).map((role) => role.slice("policy:".length));
  // A stored role the realm no longer lists stays selectable, with the warning below.
  const roleOptions = kind === "role" && value && !roles.includes(value) ? [value, ...roles] : roles;

  return (
    <fieldset className="flex flex-col gap-1 text-sm text-fg" data-scope-field="">
      <legend>{t("policies.fields.scope")}</legend>
      <div className="flex gap-2">
        <select
          className={input}
          aria-label={t("policies.fields.scopeKind")}
          value={kind}
          onChange={(e) => {
            const next = e.target.value as ScopeKind;
            onChange(scopeText(next, scopeValueFor(next, realmRoles)));
          }}
        >
          {SCOPE_KINDS.map((k) => (
            <option key={k} value={k}>
              {t(`policies.fields.scopeKinds.${k}`)}
            </option>
          ))}
        </select>
        {kind === "role" ? (
          <select
            dir="ltr"
            className={`${input} font-mono`}
            aria-label={t("policies.fields.scopeValue")}
            value={value}
            onChange={(e) => onChange(scopeText(kind, e.target.value))}
          >
            {roleOptions.length === 0 && <option value="">{t("policies.fields.noRoles")}</option>}
            {roleOptions.map((role) => (
              <option key={role} value={role}>
                {role}
              </option>
            ))}
          </select>
        ) : kind !== "global" ? (
          <input
            dir="ltr"
            className={`${input} font-mono`}
            aria-label={t("policies.fields.scopeValue")}
            value={value}
            onChange={(e) => onChange(scopeText(kind, e.target.value))}
            required
          />
        ) : null}
      </div>
      <Hint text={t(`policies.fields.scopeHints.${kind}`)} examples={SCOPE_EXAMPLES[kind]} />
      {missingRole && (
        <span role="status" className="text-[11px] text-warning">
          {t("policies.fields.unknownRole")}{" "}
          <bdi dir="ltr" className="font-mono">
            {missingRole}
          </bdi>
        </span>
      )}
    </fieldset>
  );
}
