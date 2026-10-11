// Pure helpers for the admin policy page (README §10.4). No API types are written here;
// they come from the generated gateway types.
import type { PolicyDocument, PolicyMatchRow, PolicySpan, PolicyView } from "@/lib/api/types";
import { readTargets, type Config } from "@/lib/policyConfig";

export const CHECKPOINTS = ["INPUT", "TOOL_LIST", "PRE_TOOL", "POST_TOOL", "OUTPUT", "DATA", "MODEL_TIER"] as const;
export const CHECKERS = ["regex", "presidio", "keyword_list", "llm_judge", "tier_ceiling"] as const;
export const ACTIONS = ["allow", "block", "redact", "rewrite", "warn", "require_approval", "log_only"] as const;
export const SEVERITIES = ["low", "medium", "high", "critical"] as const;
export const ON_ERROR = ["", "open", "closed"] as const;
export const TARGETS = ["answer", "memory_candidate", "summary"] as const;
export const PURPOSES = ["production", "test"] as const;

export type Segment = { text: string; entity: string | null };

/**
 * Splits `text` into plain and matched segments by the spans' character offsets.
 * Overlapping spans merge (typed by the first); spans outside the text are clamped.
 */
export function highlight(text: string, spans: PolicySpan[]): Segment[] {
  const sorted = spans
    .map((s) => ({ start: Math.max(0, Math.min(s.start, text.length)), end: Math.max(0, Math.min(s.end, text.length)), entity: s.entity }))
    .filter((s) => s.end > s.start)
    .sort((a, b) => a.start - b.start || b.end - a.end);
  const merged: { start: number; end: number; entity: string }[] = [];
  for (const span of sorted) {
    const last = merged[merged.length - 1];
    if (last && span.start < last.end) last.end = Math.max(last.end, span.end);
    else merged.push({ ...span });
  }
  const segments: Segment[] = [];
  let cursor = 0;
  for (const span of merged) {
    if (span.start > cursor) segments.push({ text: text.slice(cursor, span.start), entity: null });
    segments.push({ text: text.slice(span.start, span.end), entity: span.entity });
    cursor = span.end;
  }
  if (cursor < text.length) segments.push({ text: text.slice(cursor), entity: null });
  return segments;
}

/** The editor's state: the document with its config as editable JSON text. */
export type PolicyForm = Omit<PolicyDocument, "config"> & { configText: string };

export function emptyForm(): PolicyForm {
  return {
    id: "",
    name_ar: "",
    name_en: "",
    description_ar: "",
    description_en: "",
    purpose: "production",
    enabled: true,
    scope: "global",
    checkpoints: ["INPUT"],
    checker: "keyword_list",
    configText: JSON.stringify({ keywords: [] }, null, 2),
    action: "warn",
    severity: "medium",
    message_ar: "",
    message_en: "",
    on_error: "",
    group: "",
  };
}

export function formFromPolicy(policy: PolicyDocument): PolicyForm {
  const { config, ...rest } = policy;
  return {
    ...rest,
    description_ar: rest.description_ar ?? "",
    description_en: rest.description_en ?? "",
    purpose: rest.purpose ?? "production",
    message_ar: rest.message_ar ?? "",
    message_en: rest.message_en ?? "",
    on_error: rest.on_error ?? "",
    group: rest.group ?? "",
    configText: JSON.stringify(config ?? {}, null, 2),
  };
}

export type ParsedForm = { ok: true; policy: PolicyDocument } | { ok: false; error: "config_json" };

/** The document to send; empty optional texts are left out so the agent applies its defaults. */
export function policyFromForm(form: PolicyForm): ParsedForm {
  let config: unknown;
  try {
    config = JSON.parse(form.configText || "{}");
  } catch {
    return { ok: false, error: "config_json" };
  }
  if (typeof config !== "object" || config === null || Array.isArray(config)) return { ok: false, error: "config_json" };
  const { configText, ...rest } = form;
  void configText;
  const optional = (value: string | null | undefined) => (value && value.trim() ? value : undefined);
  return {
    ok: true,
    policy: {
      ...rest,
      version: undefined,
      config: config as PolicyDocument["config"],
      description_ar: rest.description_ar ?? "",
      description_en: rest.description_en ?? "",
      message_ar: optional(rest.message_ar),
      message_en: optional(rest.message_en),
      on_error: optional(rest.on_error),
      group: optional(rest.group),
    },
  };
}

export type PolicyMatches = { policyId: string; total: number; byAction: Record<string, number>; byDay: Record<string, number> };

/** Matches per day per policy, grouped by policy, busiest first. */
export function matchesByPolicy(rows: PolicyMatchRow[]): PolicyMatches[] {
  const byPolicy = new Map<string, PolicyMatches>();
  for (const row of rows) {
    const entry = byPolicy.get(row.policy_id) ?? { policyId: row.policy_id, total: 0, byAction: {}, byDay: {} };
    entry.total += row.matches;
    entry.byAction[row.action] = (entry.byAction[row.action] ?? 0) + row.matches;
    entry.byDay[row.day] = (entry.byDay[row.day] ?? 0) + row.matches;
    byPolicy.set(row.policy_id, entry);
  }
  return [...byPolicy.values()].sort((a, b) => b.total - a.total || a.policyId.localeCompare(b.policyId));
}

/** The last `days` UTC dates, oldest first, as YYYY-MM-DD. */
export function lastDays(days: number, today: Date = new Date()): string[] {
  const out: string[] = [];
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - i));
    out.push(d.toISOString().slice(0, 10));
  }
  return out;
}

/**
 * The policy role a `role:{name}` scope names, when the realm does not hold it (the form warns,
 * it does not refuse, ADR-0107); null for any other scope or a known role.
 */
export function unknownPolicyRole(scope: string, realmRoles: string[] | null): string | null {
  if (!scope.startsWith("role:") || realmRoles === null) return null;
  const role = `policy:${scope.slice("role:".length)}`;
  return realmRoles.includes(role) ? null : role;
}

/** A list for a reader in `locale`: the Arabic comma in Arabic, a comma in English (rule 7). */
export function formatList(items: readonly string[], locale: string): string {
  return items.join(locale === "ar" ? "، " : ", ");
}

/** The policy's name in the reader's locale (ADR-0119); both are required, the other is a fallback. */
export function policyName(policy: Pick<PolicyDocument, "name_ar" | "name_en">, locale: string): string {
  const [first, second] = locale === "ar" ? [policy.name_ar, policy.name_en] : [policy.name_en, policy.name_ar];
  return first || second;
}

/** The description in the reader's locale, the other one only when it is empty (ADR-0119). */
export function policyDescription(
  policy: Pick<PolicyDocument, "description_ar" | "description_en">,
  locale: string,
): string {
  const [first, second] =
    locale === "ar" ? [policy.description_ar, policy.description_en] : [policy.description_en, policy.description_ar];
  return first || second || "";
}

/** The admin list: test policies (smoke, TTFT) only while "Show test policies" is on (ADR-0119). */
export function visiblePolicies(views: PolicyView[], showTest: boolean): PolicyView[] {
  return showTest ? views : views.filter((view) => (view.policy.purpose ?? "production") !== "test");
}

/** The policy's own message in the reader's locale, the other one only when it is missing. */
export function messageFor(
  messages: { message_ar?: string | null; message_en?: string | null },
  locale: string,
): string | null {
  const [first, second] = locale === "ar" ? [messages.message_ar, messages.message_en] : [messages.message_en, messages.message_ar];
  return first || second || null;
}

/** Policy roles typed in the test panel: full names separated by spaces or commas, deduplicated. */
export function parsePolicyRoles(text: string): string[] {
  return [...new Set(text.split(/[\s,،]+/).filter(Boolean))].sort();
}

/** The kinds of scope (README §10.1), in the order the form lists them (ADR-0123). */
export const SCOPE_KINDS = ["global", "tenant", "role", "kb", "tool"] as const;
export type ScopeKind = (typeof SCOPE_KINDS)[number];
export const DEFAULT_TENANT = "default";

/** A scope as the form's two fields; an unknown kind is shown as global with its text kept. */
export function parseScope(scope: string): { kind: ScopeKind; value: string } {
  if (scope === "global") return { kind: "global", value: "" };
  const at = scope.indexOf(":");
  const kind = scope.slice(0, at) as ScopeKind;
  return at > 0 && SCOPE_KINDS.includes(kind) ? { kind, value: scope.slice(at + 1) } : { kind: "global", value: "" };
}

/** The scope text the agent stores: `global` has no value. */
export function scopeText(kind: ScopeKind, value: string): string {
  return kind === "global" ? "global" : `${kind}:${value.trim()}`;
}

/**
 * The value a kind starts with when the admin picks it: the default tenant, the first policy
 * role from the gateway's list (`policy:x` is the scope `role:x`), or nothing.
 */
export function scopeValueFor(kind: ScopeKind, realmRoles: string[] | null): string {
  if (kind === "tenant") return DEFAULT_TENANT;
  if (kind === "role") return (realmRoles ?? [])[0]?.slice("policy:".length) ?? "";
  return "";
}

/**
 * Who made a change (ADR-0123): the display name kept at the time, the username and id for the
 * tooltip; a row saved before names were kept shows its id.
 */
export function actorLabel(actor: { id: string; username?: string | null; name?: string | null }): {
  label: string;
  username: string | null;
  id: string;
} {
  return { label: actor.name || actor.id, username: actor.username || null, id: actor.id };
}

/** The daily matches table follows the list's "Show test policies" toggle (ADR-0123). */
export function visibleMatchRows(rows: PolicyMatchRow[], views: PolicyView[] | null, showTest: boolean): PolicyMatchRow[] {
  if (showTest || views === null) return rows;
  const test = new Set(views.filter((v) => (v.policy.purpose ?? "production") === "test").map((v) => v.policy.id));
  return rows.filter((row) => !test.has(row.policy_id));
}

/**
 * The DATA targets a policy's meta line names (ADR-0124): null when DATA is not one of its
 * checkpoints, an empty list when it covers every target (shown as one "all targets" chip).
 */
export function metaTargets(policy: Pick<PolicyDocument, "checkpoints" | "config">): string[] | null {
  if (!policy.checkpoints.includes("DATA")) return null;
  const targets = readTargets((policy.config ?? {}) as Config);
  if (targets === undefined || TARGETS.every((t) => targets.includes(t))) return [];
  return TARGETS.filter((t) => targets.includes(t));
}

/**
 * A DATA-only policy whose only target is memory_candidate does not block a reply: a block keeps
 * the candidate out of memory, so its action is named "prevent saving" (ADR-0124).
 */
export function preventsSaving(policy: Pick<PolicyDocument, "checkpoints" | "config" | "action">): boolean {
  const targets = metaTargets(policy);
  return (
    policy.action === "block" &&
    policy.checkpoints.every((c) => c === "DATA") &&
    targets !== null &&
    targets.length === 1 &&
    targets[0] === "memory_candidate"
  );
}

/** The catalog key of a policy's action label: "prevent saving" where it applies (ADR-0124). */
export function actionLabelKey(policy: Pick<PolicyDocument, "checkpoints" | "config" | "action">): string {
  return preventsSaving(policy) ? "policies.actionPreventSave" : `guardActions.${policy.action}`;
}
