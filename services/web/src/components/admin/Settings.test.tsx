import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { parse } from "yaml";
import ar from "@/i18n/ar.json";
import en from "@/i18n/en.json";
import type {
  PlatformUsage,
  ReadOnlySetting,
  SettingField,
  SettingsProblem,
  SettingsVersionView,
  TimeoutPair,
} from "@/lib/api/types";
import {
  changedKeys,
  checkNumber,
  draftValue,
  fieldKey,
  overridesOf,
  pairChips,
  restored,
  ruleFamily,
  withValue,
} from "@/lib/settings";
import { renderIn, type TestLocale } from "@/test/render";
import {
  ChangeList,
  ProblemList,
  ReadOnlyList,
  SectionHeading,
  SettingFieldRow,
  TimeoutTree,
  UsagePanel,
  VersionHistory,
} from "./SettingsParts";

// The agent's plain-words catalog (config/messages/settings.yaml); the Docker test stage copies it.
const MESSAGES = process.env.SETTINGS_MESSAGES ?? join(__dirname, "../../../../../config/messages/settings.yaml");

function field(extra: Partial<SettingField> & Pick<SettingField, "key">): SettingField {
  return {
    group: "turns",
    unit: "s",
    integer: false,
    nullable: false,
    per_tenant: false,
    minimum: 1,
    maximum: 600,
    default: 120 as never,
    value: 120 as never,
    overridden: false,
    source: "config/turn.yaml deadline_s",
    ...extra,
  } as SettingField;
}

const DEADLINE = field({ key: "turn.deadline_s" });
const PER_USER = field({ key: "turn.per_user_max", unit: "count", integer: true, maximum: 20, default: 2 as never, value: 1 as never, overridden: true });
const PER_TENANT = field({ key: "turn.per_tenant_max", unit: "count", integer: true, nullable: true, maximum: 20, default: null as never, value: null as never });
const TENANTS = field({ key: "turn.per_tenant_overrides", unit: "count", integer: true, per_tenant: true, maximum: 20, default: {} as never, value: {} as never });
const RAG = field({ key: "mcp.rag.max_concurrency", group: "mcp", unit: "count", integer: true, maximum: 8, default: 8 as never, value: 8 as never, server: "rag" });

const PAIRS: TimeoutPair[] = [
  { rule: "answer-reserve-in-turn", family: "answer-reserve-in-turn", params: {}, inner: "answer_reserve_s", inner_value: 45, outer: "deadline_s", outer_value: 120, strict: true, holds: true },
  { rule: "capability-in-room:docs", family: "capability-in-room", params: { capability: "docs" }, inner: "docs limits.timeout_s", inner_value: 60, outer: "deadline_s − answer_reserve_s", outer_value: 35, strict: true, holds: false },
  { rule: "judge-and-persist-in-post-answer", family: "judge-and-persist-in-post-answer", params: {}, inner: "timeouts.roles.judge + persist_s", inner_value: 30, outer: "post_answer_reserve_s", outer_value: 30, strict: false, holds: true },
];

const USAGE: PlatformUsage = {
  turns: { in_flight: 3, capacity: 20, per_user_max: 2, top_user_in_flight: 2, tenants: [{ tenant_id: "default", in_flight: 3 }] },
  mcp: [
    { id: "rag", max_concurrency: 6, file_max_concurrency: 8, in_use: 1, background_max: 3, background_in_use: 0, breaker: "open", open_for_s: 12.4 },
    { id: "routing", max_concurrency: 8, file_max_concurrency: 8, in_use: 0, background_max: 4, background_in_use: 0, breaker: "half_open" },
  ],
  registrations_in_background: 1,
  tasks_abandoned: 0,
} as PlatformUsage;

const READ_ONLY: ReadOnlySetting[] = [
  "lock_pool_size", "mem_limit.ingest", "mem_limit.mcp-rag-ref", "sandbox.workers", "sandbox.memory_mb",
  "sandbox.cpu_seconds", "sandbox.timeout_seconds", "sandbox.parent_reserve_mb", "turn.gateway_margin_s",
  "keep_alive.gateway_s", "keep_alive.agent_s",
].map((key) => ({ key, value: 20 as never, unit: "count", source: "docker-compose.yml" }) as ReadOnlySetting);

const VERSIONS: SettingsVersionView[] = [
  { version: 3, change: "revert", reverted_to: 2, overrides: { "turn.per_user_max": 1 } as never, created_at: "2026-10-22T10:00:00Z", created_by: "kc-1", created_by_username: "admin", created_by_name: "مديرة المنصة" },
  { version: 2, change: "save", overrides: { "turn.per_user_max": 1 } as never, created_at: "2026-10-22T09:00:00Z", created_by: "kc-1", created_by_username: "admin", created_by_name: "مديرة المنصة" },
  { version: 1, change: "seed", overrides: {} as never, created_at: "2026-10-22T08:00:00Z", created_by: "seed", created_by_username: "seed", created_by_name: "seed" },
] as SettingsVersionView[];

/** Visible text: tags dropped, technical-id chips dropped (they may hold Latin, outside any sentence). */
function sentences(html: string): string {
  return html
    .replace(/<bdi dir="ltr"[^>]*>[^<]*<\/bdi>/g, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function text(html: string): string {
  return html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}

function hasLatin(value: string): boolean {
  return /[A-Za-z]/.test(value);
}

describe("the settings page's logic", () => {
  it("keeps a value equal to its default as no override, and restoring removes it", () => {
    const overrides = overridesOf([PER_USER, DEADLINE]);
    expect(overrides).toEqual({ "turn.per_user_max": 1 });
    expect(withValue(overrides, DEADLINE, 150)).toEqual({ "turn.per_user_max": 1, "turn.deadline_s": 150 });
    expect(withValue(overrides, PER_USER, 2)).toEqual({});
    expect(restored(overrides, "turn.per_user_max")).toEqual({});
    expect(draftValue(PER_TENANT, {})).toBeNull();
    expect(changedKeys([PER_USER, DEADLINE], { "turn.deadline_s": 150 })).toEqual(["turn.per_user_max", "turn.deadline_s"]);
  });

  it("checks a typed value before anything is sent, Arabic digits included", () => {
    expect(checkNumber("١٢٠", DEADLINE)).toEqual({ value: 120, problem: null });
    expect(checkNumber("1.5", PER_USER).problem).toBe("integer");
    expect(checkNumber("21", PER_USER).problem).toBe("range");
    expect(checkNumber("abc", PER_USER).problem).toBe("number");
    expect(checkNumber("9", RAG).problem).toBe("range");  // never above the file's value
  });

  it("names catalog keys without dots and chips in a fixed order", () => {
    expect(fieldKey("mcp.rag.max_concurrency")).toBe("mcp_max_concurrency");
    expect(fieldKey("turn.per_user_max")).toBe("turn_per_user_max");
    expect(ruleFamily("tool-in-capability:docs:docs_search")).toBe("tool-in-capability");
    expect(pairChips({ params: { tool: "docs_search", capability: "docs" } })).toEqual(["docs", "docs_search"]);
  });
});

describe("the settings page's parts", () => {
  it("shows every group heading in both languages", () => {
    for (const group of ["turns", "models", "uploads", "mcp", "capacity", "tree", "usage", "history"]) {
      const arabic = text(renderIn("ar", <SectionHeading group={group} />));
      expect(arabic).toContain((ar.settings.groups as Record<string, string>)[group]);
      expect(text(renderIn("en", <SectionHeading group={group} />))).toContain(
        (en.settings.groups as Record<string, string>)[group],
      );
      expect(hasLatin(arabic), group).toBe(false);
    }
    expect(ar.settings.groups.capacity).toBe("سعة المنصة (للعرض فقط)");
  });

  it("labels a field with its unit, shows its default, a help line and the restore button", () => {
    const html = renderIn("ar", <SettingFieldRow field={PER_USER} overrides={{ "turn.per_user_max": 1 }} text="1" problem={null} />);
    const visible = sentences(html);
    expect(visible).toContain("الأسئلة الجارية للمستخدم الواحد (عدد)");
    expect(visible).toContain("الافتراضي: ٢ عدد");
    expect(visible).toContain("مثال:");
    expect(visible).toContain("استعادة الافتراضي");
    expect(visible).toContain("معدّل");
    expect(hasLatin(visible)).toBe(false);
    const seconds = sentences(renderIn("ar", <SettingFieldRow field={DEADLINE} overrides={{}} text="120" problem={null} />));
    expect(seconds).toContain("مهلة السؤال كله (ثانية)");
    expect(seconds).not.toContain("استعادة الافتراضي");
  });

  it("offers no limit of its own where null is a value, and a tenant list where a map is", () => {
    const visible = sentences(renderIn("ar", <SettingFieldRow field={PER_TENANT} overrides={{}} text="" problem={null} />));
    expect(visible).toContain("بلا حد خاص (تكفي سعة المنصة)");
    expect(visible).toContain("الافتراضي: بلا حد خاص");
    const tenants = renderIn("ar", <SettingFieldRow field={TENANTS} overrides={{}} text="" problem={null} tenantRows={[["moe", "15"]]} />);
    expect(tenants).toContain('value="moe"');
    expect(sentences(tenants)).toContain("إضافة جهة");
  });

  it("puts an MCP server's id in a chip and says its limit is never raised above the file's", () => {
    const html = renderIn("ar", <SettingFieldRow field={RAG} overrides={{}} text="8" problem="range" />);
    expect(html).toMatch(/<bdi dir="ltr"[^>]*data-chip="server"[^>]*>rag<\/bdi>/);
    const visible = sentences(html);
    expect(visible).toContain("لا يُرفع فوق قيمة ملف الإعداد: ٨");
    expect(visible).toContain("القيمة يجب أن تكون من ١ إلى ٨.");
    expect(hasLatin(visible)).toBe(false);
  });

  it("draws the timeout tree with a mark per pair and the rule's ids in chips", () => {
    const html = renderIn("ar", <TimeoutTree pairs={PAIRS} />);
    expect(html.match(/data-mark="holds"/g)).toHaveLength(2);
    expect(html.match(/data-mark="fails"/g)).toHaveLength(1);
    const visible = sentences(html);
    expect(visible).toContain("الوقت المحجوز للإجابة داخل مهلة السؤال");
    expect(visible).toContain("٤٥ ثانية أقصر من ١٢٠ ثانية");
    expect(visible).toContain("٣٠ ثانية لا تزيد على ٣٠ ثانية");
    expect(hasLatin(visible)).toBe(false);
    expect(html).toMatch(/<bdi dir="ltr"[^>]*>docs<\/bdi>/);
    expect(sentences(renderIn("ar", <TimeoutTree pairs={PAIRS} previewing />))).toContain("قبل حفظه");
  });

  it("has a title for every timeout rule and a label for every setting the agent may name", () => {
    const catalog = parse(readFileSync(MESSAGES, "utf-8")) as {
      rules: Record<string, unknown>;
      families: Record<string, unknown>;
      fields: Record<string, unknown>;
    };
    // Rules on the code, counts or memory, never pairs of seconds: not in the tree.
    const notInTree = new Set([
      "litellm-no-retries", "litellm-margin-used", "client-margin-used", "sdk-read-margin-used",
      "attempt-margin-used", "sandbox-memory", "missing",
    ]);
    for (const name of [...Object.keys(catalog.rules), ...Object.keys(catalog.families)]) {
      if (notInTree.has(name)) continue;
      expect((ar.settings.rules as Record<string, string>)[name], name).toBeTruthy();
      expect((en.settings.rules as Record<string, string>)[name], name).toBeTruthy();
    }
    for (const key of Object.keys(catalog.fields)) {
      const fields = ar.settings.fields as Record<string, { label: string; help: string }>;
      expect(fields[fieldKey(key)]?.label, key).toBeTruthy();
      expect(fields[fieldKey(key)]?.help, key).toContain("مثال:");
    }
  });

  it("shows the read-only values with their source and the redeploy note", () => {
    const html = renderIn("ar", <ReadOnlyList items={READ_ONLY} />);
    const visible = sentences(html);
    for (const item of READ_ONLY) {
      expect((ar.settings.readOnly.labels as Record<string, string>)[fieldKey(item.key)], item.key).toBeTruthy();
    }
    expect(visible).toContain("سعة المنصة من الأسئلة المتزامنة");
    expect(visible).toContain("يحتاج تغييرها إلى إعادة نشر");
    expect(hasLatin(visible)).toBe(false);
    expect(html).toContain("docker-compose.yml");
  });

  it("shows the usage as counts against their limits and each server's breaker", () => {
    const html = renderIn("ar", <UsagePanel usage={USAGE} failed={false} />);
    const visible = sentences(html);
    expect(visible).toContain("٣ من ٢٠");
    expect(visible).toContain("٢ من ٢");
    expect(visible).toContain("متوقف منذ ١٢ ثانية");
    expect(visible).toContain("في التجربة بعد توقف");
    expect(visible).toContain("١ من ٦");
    expect(hasLatin(visible)).toBe(false);
    expect(html).toMatch(/data-usage-tenant="default"/);
    const english = text(renderIn("en", <UsagePanel usage={USAGE} failed />));
    expect(english).toContain("Stopped for 12 s");
    expect(english).toContain("the last counts are shown");
  });

  it("lists the versions with their actor and time, a revert for older ones and none for the seed", () => {
    const html = renderIn("ar", <VersionHistory versions={VERSIONS} current={3} confirming={null} />);
    const visible = sentences(html);
    expect(visible).toContain("النسخة ٣");
    expect(visible).toContain("المعمول بها");
    expect(visible).toContain("استعادة النسخة ٢");
    expect(visible).toContain("مديرة المنصة");
    expect(html).toContain('data-revert="2"');
    expect(html).not.toContain('data-revert="1"');
    expect(html).not.toContain('data-revert="3"');
    const confirming = sentences(renderIn("ar", <VersionHistory versions={VERSIONS} current={3} confirming={2} />));
    expect(confirming).toContain("تأكيد الاستعادة في نسخة جديدة");
  });

  it("shows an invalid save's conflicts in the agent's plain words and what the draft changes", () => {
    const problems: SettingsProblem[] = [
      {
        rule: "answer-reserve-in-turn",
        family: "answer-reserve-in-turn",
        params: {},
        message_ar: "مهلة الإجابة (١٣٠ ثانية) أطول من مهلة السؤال كله (١٢٠ ثانية) أو تساويها، ويجب أن تكون أقصر منها.",
        message_en: "The time kept for the answer (130 s) is not shorter than the whole-question time limit (120 s).",
      } as SettingsProblem,
    ];
    for (const locale of ["ar", "en"] as TestLocale[]) {
      const visible = text(renderIn(locale, <ProblemList problems={problems} />));
      expect(visible).toContain(locale === "ar" ? "مهلة الإجابة" : "time kept for the answer");
    }
    expect(text(renderIn("ar", <ProblemList problems={[]} />))).toContain("كل قواعد المهلات سليمة");
    const changes = sentences(
      renderIn("ar", <ChangeList fields={[PER_USER]} inForce={{ "turn.per_user_max": 1 }} overrides={{}} changed={["turn.per_user_max"]} />),
    );
    expect(changes).toContain("الأسئلة الجارية للمستخدم الواحد: من ١ عدد إلى ٢ عدد");
  });
});
