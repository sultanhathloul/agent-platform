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
  const trimmed = westernDigits(text.trim());
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

// Western digits (0-9) everywhere on the page, in both languages (the owner's visual check,
// ADR-0288): one formatter for every number, date and time the page shows.
const TAGS = { ar: "ar-SA-u-nu-latn", en: "en-GB" } as const;

export type PageFormat = { number: (value: number) => string; time: (iso: string) => string };

export function pageFormat(locale: string): PageFormat {
  const tag = locale === "ar" ? TAGS.ar : TAGS.en;
  const numbers = new Intl.NumberFormat(tag, { maximumFractionDigits: 1, useGrouping: false });
  const times = new Intl.DateTimeFormat(tag, { dateStyle: "medium", timeStyle: "medium" });
  return { number: (value) => numbers.format(value), time: (iso) => times.format(new Date(iso)) };
}

/** Arabic-Indic and Persian digits typed into a field, as Western digits. */
export function westernDigits(text: string): string {
  return text.replace(/[\u0660-\u0669\u06F0-\u06F9]/g, (d) => String((d.charCodeAt(0) - 0x0660) % 0x90));
}

/** A read-only value's chip: the file's name and the key ("docker-compose.yml · mem_limit"); the full source is its tooltip. */
export function sourceChip(source: string): string {
  const [path = source, ...rest] = source.replace(/\s*\(.*\)\s*$/, "").trim().split(/\s+/);
  const file = path.split("/").pop() ?? path;
  const last = rest.at(-1);
  if (!last) return file;
  return `${file} · ${last.split(".").pop() ?? last}`;
}

/** One row of the timeout tree: a pair with its depth, and whether a pair under it fails. */
export type TreeRow = { pair: TimeoutPair; depth: number; path: string; failsBelow: boolean };

/**
 * The pairs as a tree (ADR-0286): each pair under each pair it names as a parent, in the rules'
 * order; a pair whose parents are all missing is a root. A pair under two parents (an MCP server
 * under each tool on it) shows under both.
 */
export function treeRows(pairs: TimeoutPair[]): TreeRow[] {
  const known = new Set(pairs.map((p) => p.rule));
  const children = new Map<string, TimeoutPair[]>();
  const roots: TimeoutPair[] = [];
  for (const pair of pairs) {
    const parents = (pair.parents ?? []).filter((p) => known.has(p) && p !== pair.rule);
    if (parents.length === 0) roots.push(pair);
    for (const parent of parents) children.set(parent, [...(children.get(parent) ?? []), pair]);
  }
  const out: TreeRow[] = [];
  const visit = (pair: TimeoutPair, depth: number, path: string, seen: Set<string>): boolean => {
    const row: TreeRow = { pair, depth, path, failsBelow: false };
    out.push(row);
    let fails = false;
    for (const child of children.get(pair.rule) ?? []) {
      if (seen.has(child.rule)) continue;
      const below = visit(child, depth + 1, `${path}/${child.rule}`, new Set([...seen, child.rule]));
      fails = below || !child.holds || fails;
    }
    row.failsBelow = fails;
    return fails || !pair.holds;
  };
  for (const root of roots) visit(root, 0, root.rule, new Set([root.rule]));
  return out;
}

/** The usage section refreshes this often while the page is open (item 7b). */
export const USAGE_REFRESH_MS = 5000;
