// The platform settings page's logic (owner decision 2026-10-22, ADR-0276): the draft of overrides,
// what a value in a field means, and the catalog keys of each label. The agent is the authority:
// every save is checked there with the timeout budget's rules; the checks here only say early what
// a field cannot hold.
import type { SettingField, TimeoutPair } from "@/lib/api/types";

export const GROUPS = ["turns", "models", "uploads", "mcp"] as const;
export type Group = (typeof GROUPS)[number];

/** A setting's value: seconds or a count, a map from tenant id to a count, or null (no limit of its own). */
export type SettingValue = number | Record<string, number> | null;
export type Overrides = Record<string, SettingValue>;

// A tenant id as the agent accepts it (app/resilience/budget.py TENANT_ID).
const TENANT_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;

export function valueOf(raw: unknown): SettingValue {
  if (raw === null || raw === undefined) return null;
  if (typeof raw === "number") return raw;
  if (typeof raw === "object") return { ...(raw as Record<string, number>) };
  return null;
}

/** The overrides in force: one per field the agent reports as overridden. */
export function overridesOf(fields: SettingField[]): Overrides {
  const out: Overrides = {};
  for (const field of fields) if (field.overridden) out[field.key] = valueOf(field.value);
  return out;
}

export function sameValue(a: SettingValue, b: SettingValue): boolean {
  if (a === null || b === null || typeof a === "number" || typeof b === "number") return a === b;
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  return [...keys].every((k) => a[k] === b[k]);
}

/** `overrides` with `field` set to `value`; a value equal to the default is no override. */
export function withValue(overrides: Overrides, field: SettingField, value: SettingValue): Overrides {
  const next = { ...overrides };
  if (sameValue(value, valueOf(field.default))) delete next[field.key];
  else next[field.key] = value;
  return next;
}

/** "استعادة الافتراضي": the field goes back to the files' value. */
export function restored(overrides: Overrides, key: string): Overrides {
  const next = { ...overrides };
  delete next[key];
  return next;
}

/** The value a field shows for a draft: its override, else its default. */
export function draftValue(field: SettingField, overrides: Overrides): SettingValue {
  const own = overrides[field.key];
  return own !== undefined ? own : valueOf(field.default);
}

/** The keys whose value the draft changes against the overrides in force. */
export function changedKeys(fields: SettingField[], overrides: Overrides): string[] {
  const inForce = overridesOf(fields);
  return fields
    .filter((f) => !sameValue(draftValue(f, overrides), draftValue(f, inForce)))
    .map((f) => f.key);
}

export type FieldProblem = "number" | "integer" | "range" | "tenant" | null;

/** What a typed number cannot be for this field, before any call; null when it may be sent. */
export function checkNumber(text: string, field: SettingField): { value: number | null; problem: FieldProblem } {
  const trimmed = text.trim().replace(/[٠-٩]/g, (d) => String(d.charCodeAt(0) - 0x0660));
  if (trimmed === "" || !/^\d+(\.\d+)?$/.test(trimmed)) return { value: null, problem: "number" };
  const value = Number(trimmed);
  if (field.integer && !Number.isInteger(value)) return { value, problem: "integer" };
  if (value < field.minimum || value > field.maximum) return { value, problem: "range" };
  return { value, problem: null };
}

export function validTenantId(id: string): boolean {
  return TENANT_ID.test(id);
}

/** The catalog key of a setting's label and help: dots are path separators in the catalogs. */
export function fieldKey(key: string): string {
  if (key.startsWith("mcp.") && key.endsWith(".max_concurrency")) return "mcp_max_concurrency";
  return key.replace(/\./g, "_");
}

export function unitKey(unit: string): string {
  return `settings.units.${unit}`;
}

/** A rule's family: its name before the first colon (a generated rule names its subject after it). */
export function ruleFamily(rule: string): string {
  return rule.split(":", 1)[0] ?? rule;
}

/** The ids a pair names, in a fixed order, for its chips. */
export function pairChips(pair: Pick<TimeoutPair, "params">): string[] {
  const params = pair.params ?? {};
  return ["capability", "tool", "server", "service"].flatMap((k) => (params[k] ? [params[k]] : []));
}

/** Fields of a group, in the agent's order. */
export function byGroup(fields: SettingField[], group: Group): SettingField[] {
  return fields.filter((f) => f.group === group);
}

/** A number as the page shows it: up to one decimal, in the locale's digits. */
export function formatNumber(value: number, locale: string): string {
  return new Intl.NumberFormat(locale === "ar" ? "ar-SA" : "en-GB", { maximumFractionDigits: 1 }).format(value);
}

export function formatTime(iso: string, locale: string): string {
  return new Date(iso).toLocaleString(locale === "ar" ? "ar-SA" : "en-GB");
}

/** The usage section refreshes this often while the page is open (item 7b). */
export const USAGE_REFRESH_MS = 5000;
