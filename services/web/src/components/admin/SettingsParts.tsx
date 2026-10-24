"use client";

// The platform settings page's parts (owner decision 2026-10-22, items 6 and 7): code-free, Arabic
// labels with units, a help line with an example under each field, the default beside each value,
// technical ids only in chips. Each part renders from data alone, so a test renders it as is.
import { useLocale, useTranslations } from "next-intl";
import { useMemo } from "react";
import type {
  PlatformUsage,
  ReadOnlySetting,
  SettingField,
  SettingsProblem,
  SettingsVersionView,
  TimeoutPair,
} from "@/lib/api/types";
import {
  type FieldProblem,
  type SettingValue,
  draftValue,
  fieldKey,
  pageFormat,
  pairChips,
  ruleFamily,
  sourceChip,
  treeRows,
  unitKey,
  valueOf,
  type Overrides,
  type PageFormat,
} from "@/lib/settings";
import { ActorName } from "./ActorName";

export const card = "rounded-2xl bg-white p-4 shadow-sm ring-1 ring-slate-200";
export const button =
  "rounded-md bg-emerald-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-800 disabled:opacity-50";
export const quiet =
  "rounded-md px-3 py-1.5 text-sm text-slate-700 ring-1 ring-slate-300 hover:bg-slate-50 disabled:opacity-50";
const input = "w-28 rounded-md border border-slate-300 px-2 py-1.5 text-sm";
const chip = "rounded-full bg-slate-100 px-1.5 text-[11px] text-slate-600";
// Latin identifiers only: a monospace face never holds Arabic text (ADR-0119).
const code = `${chip} font-mono`;

/** A technical id, outside any sentence (item 6). */
export function Chip({ children, name }: { children: string; name?: string }) {
  return (
    <bdi dir="ltr" className={code} data-chip={name ?? ""}>
      {children}
    </bdi>
  );
}

/** The page's one formatter (ADR-0288): Western digits in both languages. */
export function usePageFormat(): PageFormat {
  const locale = useLocale();
  return useMemo(() => pageFormat(locale), [locale]);
}

/** A value as the page shows it: a number with its unit, "no limit of its own", or a count of tenants. */
export function useValueText() {
  const t = useTranslations("settings");
  const f = usePageFormat();
  return (value: SettingValue, unit: string): string => {
    if (value === null) return t("review.noneValue");
    if (typeof value === "object") return t("review.tenantsValue", { count: f.number(Object.keys(value).length) });
    return `${f.number(value)} ${t(unitKey(unit).replace("settings.", ""))}`;
  };
}

export function SectionHeading({ group }: { group: string }) {
  const t = useTranslations("settings");
  return (
    <header className="mb-3">
      <h2 className="text-base font-semibold">{t(`groups.${group}`)}</h2>
      <p className="mt-0.5 text-xs text-slate-500">{t(`groupHints.${group}`)}</p>
    </header>
  );
}

type FieldProps = {
  field: SettingField;
  overrides: Overrides;
  text: string;
  problem: FieldProblem;
  onText?: (text: string) => void;
  onNoLimit?: (on: boolean) => void;
  onTenants?: (rows: Array<[string, string]>) => void;
  tenantRows?: Array<[string, string]>;
  onRestore?: () => void;
};

/** One editable setting: its label with its unit, its input, its default and a help line. */
export function SettingFieldRow({
  field,
  overrides,
  text,
  problem,
  onText,
  onNoLimit,
  onTenants,
  tenantRows = [],
  onRestore,
}: FieldProps) {
  const t = useTranslations("settings");
  const f = usePageFormat();
  const valueText = useValueText();
  const key = fieldKey(field.key);
  const value = draftValue(field, overrides);
  const defaultValue = valueOf(field.default);
  const edited = field.key in overrides;
  const unit = t(unitKey(field.unit).replace("settings.", ""));
  const id = `setting-${field.key}`;
  const range = { minimum: f.number(field.minimum), maximum: f.number(field.maximum) };
  return (
    <div className="border-t border-slate-100 py-3 first:border-t-0" data-field={field.key}>
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor={id} className="text-sm font-medium">
          {t("fieldLabel", { label: t(`fields.${key}.label`), unit })}
        </label>
        {field.server && <Chip name="server">{field.server}</Chip>}
        {edited && (
          <span className="rounded-full bg-amber-100 px-1.5 text-[11px] text-amber-900" data-badge="overridden">
            {t("overridden")}
          </span>
        )}
      </div>
      {field.per_tenant ? (
        <TenantRows id={id} rows={tenantRows} onChange={onTenants} />
      ) : (
        <div className="mt-1.5 flex flex-wrap items-center gap-3">
          <input
            id={id}
            inputMode="decimal"
            dir="ltr"
            className={input}
            value={value === null ? "" : text}
            disabled={value === null}
            aria-invalid={problem !== null}
            onChange={(e) => onText?.(e.target.value)}
          />
          {field.nullable && (
            <label className="flex items-center gap-1.5 text-sm text-slate-700">
              <input type="checkbox" checked={value === null} onChange={(e) => onNoLimit?.(e.target.checked)} />
              {t("noLimit")}
            </label>
          )}
        </div>
      )}
      <p className="mt-1 text-xs text-slate-600" data-default="">
        {field.per_tenant
          ? t("defaultTenants")
          : defaultValue === null
            ? t("defaultNone")
            : t("default", { value: valueText(defaultValue, field.unit) })}
        {" · "}
        {field.group === "mcp" ? t("maximumFile", range) : t("maximum", range)}
      </p>
      <p className="mt-0.5 text-xs text-slate-500">{t(`fields.${key}.help`)}</p>
      {problem && (
        <p role="alert" className="mt-1 text-xs text-red-700" data-problem={problem}>
          {t(`problems.${problem}`, range)}
        </p>
      )}
      {edited && (
        <button type="button" className={`${quiet} mt-2`} onClick={onRestore} data-restore={field.key}>
          {t("restore")}
        </button>
      )}
    </div>
  );
}

function TenantRows({
  id,
  rows,
  onChange,
}: {
  id: string;
  rows: Array<[string, string]>;
  onChange?: (rows: Array<[string, string]>) => void;
}) {
  const t = useTranslations("settings.tenants");
  return (
    <div className="mt-1.5 space-y-1.5" id={id}>
      {rows.length === 0 && <p className="text-xs text-slate-500">{t("none")}</p>}
      {rows.map(([tenant, count], index) => (
        <div key={index} className="flex flex-wrap items-center gap-2" data-tenant-row="">
          <input
            aria-label={t("tenantId")}
            dir="ltr"
            className={`${input} w-40 font-mono`}
            value={tenant}
            onChange={(e) => onChange?.(rows.map((r, i) => (i === index ? [e.target.value, r[1]] : r)))}
          />
          <input
            aria-label={t("limit")}
            dir="ltr"
            inputMode="numeric"
            className={input}
            value={count}
            onChange={(e) => onChange?.(rows.map((r, i) => (i === index ? [r[0], e.target.value] : r)))}
          />
          <button type="button" className={quiet} onClick={() => onChange?.(rows.filter((_, i) => i !== index))}>
            {t("remove")}
          </button>
        </div>
      ))}
      <button type="button" className={quiet} onClick={() => onChange?.([...rows, ["", ""]])}>
        {t("add")}
      </button>
    </div>
  );
}

/** "سعة المنصة (للعرض فقط)": what only a redeploy changes, with where it is set (item 2). */
export function ReadOnlyList({ items }: { items: ReadOnlySetting[] }) {
  const t = useTranslations("settings");
  const valueText = useValueText();
  return (
    <ul className="divide-y divide-slate-100" data-section="capacity">
      {items.map((item) => (
        <li
          key={item.key}
          className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm lg:flex-nowrap"
          data-readonly={item.key}
        >
          <span className="min-w-0">{t(`readOnly.labels.${fieldKey(item.key)}`)}</span>
          {/* At 1024 px and wider the value, the note and the chip stay on one line (the owner's check, item 2). */}
          <span className="flex flex-wrap items-center gap-2 lg:shrink-0 lg:flex-nowrap lg:whitespace-nowrap">
            <span className="font-medium" data-value="">
              {valueText(typeof item.value === "number" ? item.value : null, item.unit)}
            </span>
            <span className="text-xs text-slate-500">{t("readOnly.redeploy")}</span>
            <span title={t("readOnly.source", { source: item.source })}>
              <Chip name="source">{sourceChip(item.source)}</Chip>
            </span>
          </span>
        </li>
      ))}
    </ul>
  );
}

/**
 * "شجرة المهلات": each pair nested under the pair whose inner bound is its outer one (ADR-0286),
 * with its two values and a pass or fail mark; a row with a failing pair under it also shows a
 * warning mark (item 7a, the owner's check, item 4).
 */
export function TimeoutTree({ pairs, previewing = false }: { pairs: TimeoutPair[]; previewing?: boolean }) {
  const t = useTranslations("settings");
  const f = usePageFormat();
  const seconds = (v: number) => `${f.number(v)} ${t("units.s")}`;
  return (
    <div data-section="tree">
      {previewing && <p className="mb-2 text-xs text-amber-800">{t("tree.previewing")}</p>}
      <ul role="tree" className="divide-y divide-slate-100">
        {treeRows(pairs).map(({ pair, depth, path, failsBelow }) => {
          const family = ruleFamily(pair.rule);
          const title = t.has(`rules.${pair.rule}`)
            ? t(`rules.${pair.rule}`)
            : t.has(`rules.${family}`)
              ? t(`rules.${family}`)
              : t("tree.other");
          return (
            <li
              key={path}
              role="treeitem"
              aria-level={depth + 1}
              aria-selected={false}
              className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm"
              style={{ paddingInlineStart: `${depth * 1.5}rem` }}
              data-rule={pair.rule}
              data-depth={depth}
            >
              <span className="flex flex-wrap items-center gap-1.5">
                {depth > 0 && (
                  <span aria-hidden="true" className="mb-1.5 inline-block h-2.5 w-2.5 border-b border-s border-slate-300" />
                )}
                <span
                  className={`rounded-full px-1.5 text-[11px] ${pair.holds ? "bg-emerald-100 text-emerald-900" : "bg-red-100 text-red-900"}`}
                  data-mark={pair.holds ? "holds" : "fails"}
                >
                  {t(pair.holds ? "tree.holds" : "tree.fails")}
                </span>
                {failsBelow && (
                  <span className="rounded-full bg-amber-100 px-1.5 text-[11px] text-amber-900" data-mark="warning">
                    {t("tree.failsBelow")}
                  </span>
                )}
                <span>{title}</span>
                {pairChips(pair).map((id) => (
                  <Chip key={id}>{id}</Chip>
                ))}
              </span>
              <span className="text-slate-700" title={`${pair.inner} · ${pair.outer}`}>
                {t(pair.strict ? "tree.strict" : "tree.loose", {
                  inner: seconds(pair.inner_value),
                  outer: seconds(pair.outer_value),
                })}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** Why a draft cannot be saved: the agent's sentences, which name each conflict in plain words. */
export function ProblemList({ problems }: { problems: SettingsProblem[] }) {
  const t = useTranslations("settings.review");
  const locale = useLocale();
  if (problems.length === 0) return <p className="text-sm text-emerald-800">{t("valid")}</p>;
  return (
    <div role="alert" data-section="problems">
      <p className="text-sm font-medium text-red-800">{t("invalid")}</p>
      <ul className="mt-1 list-inside list-disc space-y-1 text-sm text-red-800">
        {problems.map((problem, index) => (
          <li key={`${problem.rule}-${index}`} data-problem-rule={problem.rule}>
            {locale === "ar" ? problem.message_ar : problem.message_en}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** What the draft changes, each value from the one in force to the new one. */
export function ChangeList({
  fields,
  inForce,
  overrides,
  changed,
}: {
  fields: SettingField[];
  inForce: Overrides;
  overrides: Overrides;
  changed: string[];
}) {
  const t = useTranslations("settings");
  const valueText = useValueText();
  if (changed.length === 0) return <p className="text-sm text-slate-600">{t("review.none")}</p>;
  return (
    <ul className="space-y-1 text-sm" data-section="changes">
      {fields
        .filter((f) => changed.includes(f.key))
        .map((field) => (
          <li key={field.key} data-change={field.key} className="flex flex-wrap items-center gap-1.5">
            <span>
              {t("review.line", {
                field: t(`fields.${fieldKey(field.key)}.label`),
                before: valueText(draftValue(field, inForce), field.unit),
                after: valueText(draftValue(field, overrides), field.unit),
              })}
            </span>
            {field.server && <Chip name="server">{field.server}</Chip>}
          </li>
        ))}
    </ul>
  );
}

/** "الاستهلاك الآن": counts only, from the agent's memory (item 7b). */
export function UsagePanel({ usage, failed, updatedAt }: { usage: PlatformUsage | null; failed: boolean; updatedAt?: string }) {
  const t = useTranslations("settings.usage");
  const f = usePageFormat();
  const n = f.number;
  const of = (count: number, limit: number | null | undefined) =>
    limit === null || limit === undefined ? t("noOwnLimit", { count: n(count) }) : t("ofLimit", { count: n(count), limit: n(limit) });
  return (
    <div data-section="usage" className="space-y-3 text-sm">
      {failed && <p role="status" className="text-xs text-amber-800">{t("failed")}</p>}
      {usage && (
        <>
          <dl className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <div>
              <dt className="text-slate-500">{t("turns")}</dt>
              <dd className="font-medium" data-usage="turns">{of(usage.turns.in_flight, usage.turns.capacity)}</dd>
            </div>
            <div>
              <dt className="text-slate-500">{t("topUser")}</dt>
              <dd className="font-medium" data-usage="top-user">{of(usage.turns.top_user_in_flight, usage.turns.per_user_max)}</dd>
            </div>
            <div>
              <dt className="text-slate-500">{t("registrations")}</dt>
              <dd className="font-medium" data-usage="registrations">{n(usage.registrations_in_background)}</dd>
            </div>
            <div>
              <dt className="text-slate-500">{t("abandoned")}</dt>
              <dd className="font-medium" data-usage="abandoned">{n(usage.tasks_abandoned)}</dd>
            </div>
          </dl>
          <div>
            <p className="text-slate-500">{t("perTenant")}</p>
            {usage.turns.tenants.length === 0 ? (
              <p className="text-xs text-slate-500">{t("noTenants")}</p>
            ) : (
              <ul className="mt-1 space-y-1">
                {usage.turns.tenants.map((tenant) => (
                  <li key={tenant.tenant_id} className="flex items-center gap-2" data-usage-tenant={tenant.tenant_id}>
                    <Chip name="tenant">{tenant.tenant_id}</Chip>
                    <span>{of(tenant.in_flight, tenant.limit ?? usage.turns.capacity)}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          {usage.mcp.length === 0 ? (
            <p className="text-xs text-slate-500">{t("noServers")}</p>
          ) : (
            <ul className="space-y-2">
              {usage.mcp.map((server) => (
                <li key={server.id} className="flex flex-wrap items-center gap-x-4 gap-y-1" data-usage-server={server.id}>
                  <Chip name="server">{server.id}</Chip>
                  <span>{t("labelled", { label: t("slots"), value: of(server.in_use, server.max_concurrency) })}</span>
                  <span>
                    {t("labelled", {
                      label: t("background"),
                      value: of(server.background_in_use, server.background_max),
                    })}
                  </span>
                  <span data-breaker={server.breaker}>
                    {t("labelled", {
                      label: t("breaker"),
                      value:
                        server.breaker === "open"
                          ? t("open", { seconds: n(Math.round(server.open_for_s ?? 0)) })
                          : t(server.breaker === "half_open" ? "half_open" : "closed"),
                    })}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {updatedAt && <p className="text-xs text-slate-400">{t("updated", { time: f.time(updatedAt) })}</p>}
        </>
      )}
    </div>
  );
}

/** "سجل النسخ": every version, newest first, with its actor and time; a revert is a new version. */
export function VersionHistory({
  versions,
  current,
  confirming,
  busy = false,
  onRevert,
  onConfirm,
  onCancel,
}: {
  versions: SettingsVersionView[];
  current: number;
  confirming: number | null;
  busy?: boolean;
  onRevert?: (version: number) => void;
  onConfirm?: () => void;
  onCancel?: () => void;
}) {
  const t = useTranslations("settings.history");
  const f = usePageFormat();
  if (versions.length === 0) return <p className="text-sm text-slate-600">{t("empty")}</p>;
  return (
    <ol className="divide-y divide-slate-100" data-section="history">
      {versions.map((v) => {
        const overrides = Object.keys(v.overrides ?? {});
        return (
          <li key={v.version} className="py-2 text-sm" data-version={v.version}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{t("version", { version: f.number(v.version) })}</span>
              {v.version === current && (
                <span className="rounded-full bg-emerald-100 px-1.5 text-[11px] text-emerald-900">{t("current")}</span>
              )}
              <span>
                {v.change === "revert"
                  ? t("changes.revert", { version: f.number(v.reverted_to ?? 0) })
                  : t(`changes.${v.change}`)}
              </span>
              {v.change === "seed" ? (
                // Version 1 comes from the files, not from a person (the owner's check, item 3).
                <span data-actor="files">{t("filesActor")}</span>
              ) : (
                <ActorName id={v.created_by} username={v.created_by_username} name={v.created_by_name} />
              )}
              <span className="text-xs text-slate-500">{f.time(v.created_at)}</span>
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-1">
              <span className="text-xs text-slate-600">{t("overrides", { count: f.number(overrides.length) })}</span>
              {overrides.map((key) => (
                <Chip key={key}>{key}</Chip>
              ))}
            </div>
            {v.version !== current && v.change !== "seed" && (
              <div className="mt-1.5 flex gap-2">
                {confirming === v.version ? (
                  <>
                    <button type="button" className={button} disabled={busy} onClick={onConfirm}>
                      {t("revertConfirm")}
                    </button>
                    <button type="button" className={quiet} onClick={onCancel}>
                      {t("cancel")}
                    </button>
                  </>
                ) : (
                  <button type="button" className={quiet} onClick={() => onRevert?.(v.version)} data-revert={v.version}>
                    {t("revert")}
                  </button>
                )}
              </div>
            )}
          </li>
        );
      })}
    </ol>
  );
}
