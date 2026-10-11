import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { describe, expect, it } from "vitest";
import { parse } from "yaml";
import type { PolicyDocument, PolicyView } from "@/lib/api/types";
import { formFromPolicy, parseScope, policyFromForm, scopeText, scopeValueFor, visibleMatchRows } from "@/lib/policies";
import {
  EDITOR_FIELDS,
  TARGETS_FIELD,
  addKeywords,
  configProblems,
  configToText,
  parseConfig,
  readEntities,
  readKeywords,
  starterConfig,
  toggleIn,
  withField,
  type Config,
} from "@/lib/policyConfig";
import { CATALOGS, renderIn } from "@/test/render";
import { ActorName } from "./ActorName";
import { ConfigEditor } from "./ConfigEditor";
import { ConfigSummary } from "./ConfigSummary";
import { MatchesPerDay } from "./PolicyAdmin";
import { PolicyList } from "./PolicyList";
import { Hint, ScopeField } from "./ScopeField";
import { Explanation, OutputHeading } from "./TestResultParts";

// The seed policies of config/policies (the Docker test stage copies them, ADR-0123).
const SEED_DIR = process.env.SEED_POLICIES_DIR ?? join(__dirname, "../../../../../config/policies");

function seeds(): PolicyDocument[] {
  return readdirSync(SEED_DIR)
    .filter((name) => name.endsWith(".yaml"))
    .sort()
    .flatMap((name) => {
      const doc = parse(readFileSync(join(SEED_DIR, name), "utf-8"));
      return Array.isArray(doc) ? doc : (doc.policies ?? [doc]);
    });
}

// No seed uses these two checkers; their editors round-trip these configs instead.
const EXTRA: PolicyDocument[] = [
  {
    id: "keyword-editor",
    name_ar: "قائمة كلمات",
    name_en: "Keyword list",
    scope: "global",
    checkpoints: ["INPUT", "DATA"],
    checker: "keyword_list",
    config: { keywords: ["كلمة", "عبارة أخرى"], mode: "deny", targets: ["answer"] },
    action: "warn",
  },
  {
    id: "tier-editor",
    name_ar: "سقف المستوى",
    name_en: "Tier ceiling",
    scope: "role:viewer",
    checkpoints: ["MODEL_TIER"],
    checker: "tier_ceiling",
    config: { max_tier: "standard", roles: ["reasoning", "reflection"] },
    action: "rewrite",
  },
];

/** What an editor writes when every field it shows is saved as it was loaded. */
function throughEditor(checker: string, config: Config, checkpoints: string[]): Config {
  const fields = [...(EDITOR_FIELDS[checker] ?? []), ...(checkpoints.includes("DATA") ? [TARGETS_FIELD] : [])];
  return fields.reduce((out, field) => withField(out, field.key, field.read(out)), config);
}

const editor = (locale: "ar" | "en", policy: Pick<PolicyDocument, "checker" | "checkpoints">, configText: string) =>
  renderIn(locale, createElement(ConfigEditor, { checker: policy.checker, configText, checkpoints: policy.checkpoints, onChange: () => {} }));

describe("the configuration editors round-trip the seed configs (ADR-0123)", () => {
  const all = [...seeds(), ...EXTRA];

  it("reads every seed, and covers every checker", () => {
    expect(seeds().length).toBeGreaterThanOrEqual(8);
    expect(new Set(all.map((p) => p.checker))).toEqual(new Set(Object.keys(EDITOR_FIELDS)));
  });

  for (const policy of [...seeds(), ...EXTRA]) {
    it(`${policy.id} (${policy.checker}): load, save unchanged, identical config`, () => {
      const config = policy.config as Config;
      expect(throughEditor(policy.checker, config, policy.checkpoints)).toEqual(config);
      const saved = policyFromForm(formFromPolicy(policy));
      expect(saved.ok && saved.policy.config).toEqual(config);
      expect(configProblems(policy.checker, config, policy.checkpoints)).toEqual([]);
      // The editor shows the config: no problem, no paused state, no forced JSON view.
      const html = editor("ar", policy, configToText(config));
      expect(html).toContain(`data-config-editor="${policy.checker}"`);
      expect(html).not.toContain("data-problem");
      expect(html).not.toContain("data-advanced-json");
    });
  }

  it("shows each editor's values: entities, rows, examples, tags, tier and targets", () => {
    const byId = Object.fromEntries(all.map((p) => [p.id, p]));
    const presidio = byId["pii-redact-output"]!;
    const html = editor("ar", presidio, configToText(presidio.config as Config));
    for (const entity of ["SA_MOBILE", "SA_NATIONAL_ID", "SA_IBAN", "EMAIL_ADDRESS"]) {
      expect(html).toMatch(new RegExp(`data-entity="${entity}"><input type="checkbox" checked=""`));
      expect(html).toContain(CATALOGS.ar.policies.entities[entity as keyof typeof CATALOGS.ar.policies.entities]);
    }
    expect(html).toMatch(/data-entity="ARABIC_NAME"><input type="checkbox"\/>/);
    expect(html).toMatch(/data-target="answer"><input type="checkbox" checked=""/);
    expect(html).toMatch(/data-target="memory_candidate"><input type="checkbox"\/>/);
    expect(html).toContain('value="0.5"');

    const regex = byId["prompt-injection-input"]!;
    const rows = (regex.config as { patterns: unknown[] }).patterns.length;
    expect(editor("ar", regex, configToText(regex.config as Config)).match(/data-pattern-row=/g)).toHaveLength(rows);

    const judge = byId["topics-politics-llm-judge"]!;
    const examples = (judge.config as { examples: unknown[] }).examples.length;
    const judgeHtml = editor("ar", judge, configToText(judge.config as Config));
    expect(judgeHtml.match(/data-example-row=/g)).toHaveLength(examples);
    expect(judgeHtml).toContain(CATALOGS.ar.policies.config.violates);

    const keywords = editor("ar", EXTRA[0]!, configToText(EXTRA[0]!.config as Config));
    expect(keywords).toContain("عبارة أخرى");
    expect(keywords).toContain(`placeholder="${CATALOGS.ar.policies.editor.keywordPlaceholder}"`);

    const tier = editor("ar", EXTRA[1]!, configToText(EXTRA[1]!.config as Config));
    expect(tier).toContain(`<option value="standard" selected="">${CATALOGS.ar.policies.tiers.standard}</option>`);
    expect(tier).toMatch(/data-model-role="reasoning"><input type="checkbox" checked=""/);
    expect(tier).toMatch(/data-model-role="router"><input type="checkbox"\/>/);
  });

  it("keeps keys it does not edit, and unknown entities", () => {
    const config = { entities: ["SA_MOBILE", "CUSTOM_X"], score_threshold: 0.7, language: "en" };
    const next = withField(config, "entities", ["SA_MOBILE", "SA_IBAN", "CUSTOM_X"]);
    expect(next).toEqual({ entities: ["SA_MOBILE", "SA_IBAN", "CUSTOM_X"], score_threshold: 0.7, language: "en" });
    expect(addKeywords(["أ"], " ب، ج\nأ ")).toEqual(["أ", "ب", "ج"]);
    expect(toggleIn(undefined, ["answer", "summary", "memory_candidate"], "summary", false)).toEqual(["answer", "memory_candidate"]);
    expect(starterConfig("presidio", { keywords: ["x"], targets: ["summary"] })).toEqual({ entities: [], targets: ["summary"] });
  });
});

describe("the editors' validation (ADR-0123)", () => {
  const cases: [string, Config, string[], string][] = [
    ["keyword_list", { keywords: [] }, ["INPUT"], "keywordsEmpty"],
    ["regex", { patterns: [] }, ["INPUT"], "patternsEmpty"],
    ["regex", { patterns: [{ name: "BAD NAME", pattern: "x" }] }, ["INPUT"], "patternName"],
    ["regex", { patterns: [{ name: "BROKEN", pattern: "(unclosed" }] }, ["INPUT"], "patternInvalid"],
    ["presidio", { entities: [] }, ["OUTPUT"], "entitiesEmpty"],
    ["presidio", { entities: ["SA_MOBILE"], score_threshold: 1.5 }, ["OUTPUT"], "threshold"],
    ["llm_judge", { topic_description: " " }, ["INPUT"], "topicEmpty"],
    ["llm_judge", { topic_description: "موضوع", examples: [{ text: "", violates: true }] }, ["INPUT"], "exampleEmpty"],
    ["tier_ceiling", { max_tier: "" }, ["MODEL_TIER"], "tierEmpty"],
    ["presidio", { entities: ["SA_MOBILE"], targets: [] }, ["DATA"], "targetsEmpty"],
  ];

  for (const [checker, config, checkpoints, key] of cases) {
    it(`${key}: refused, shown inline from the catalog in ar and en`, () => {
      expect(configProblems(checker, config, checkpoints).map((p) => p.key)).toContain(key);
      for (const locale of ["ar", "en"] as const) {
        const message = CATALOGS[locale].policies.editor.errors[key as keyof typeof CATALOGS.ar.policies.editor.errors];
        expect(message).toBeTruthy();
        const html = editor(locale, { checker, checkpoints }, configToText(config));
        expect(html).toContain(`data-problem="${key}">${message.replaceAll("'", "&#x27;")}</span>`);
      }
      expect(CATALOGS.ar.policies.editor.errors[key as keyof typeof CATALOGS.ar.policies.editor.errors]).not.toMatch(/[A-Za-z]/);
    });
  }

  it("accepts Python named groups, which the agent's validator decides", () => {
    expect(configProblems("regex", { patterns: [{ name: "N", pattern: "(?P<d>\\d+)(?P=d)" }] }, ["INPUT"])).toEqual([]);
  });

  it("invalid JSON pauses the editor, shows the JSON view and blocks saving, with an Arabic message", () => {
    const text = '{"entities": ["SA_MOBILE"';
    expect(parseConfig(text).ok).toBe(false);
    expect(policyFromForm({ ...formFromPolicy(seeds()[0]!), configText: text }).ok).toBe(false);
    const html = editor("ar", { checker: "presidio", checkpoints: ["OUTPUT"] }, text);
    expect(html).toContain(`data-problem="paused">${CATALOGS.ar.policies.editor.paused}</p>`);
    expect(html).toContain(`data-problem="jsonInvalid">${CATALOGS.ar.policies.editor.jsonInvalid}</span>`);
    expect(html).toMatch(/<fieldset disabled=""/);
    expect(html).toContain("data-advanced-json");
    expect(CATALOGS.ar.policies.editor.jsonInvalid).not.toMatch(/[A-Za-z]/);
    expect(CATALOGS.ar.policies.configInvalid).not.toMatch(/[A-Za-z]/);
  });
});

describe("the advanced JSON view and the editor stay in sync (ADR-0123)", () => {
  it("an editor change is the JSON text, and a JSON edit is what the editor shows", () => {
    const policy = seeds().find((p) => p.id === "pii-block-in-memory")!;
    const original = configToText(policy.config as Config);
    // The editor switches SA_IBAN off: the JSON view's text is the new config.
    const config = parseConfig(original);
    if (!config.ok) throw new Error("seed config");
    const edited = configToText(withField(config.config, "entities", readEntities(config.config).filter((e) => e !== "SA_IBAN")));
    const back = parseConfig(edited);
    expect(back.ok && readEntities(back.config)).toEqual(["SA_MOBILE", "SA_NATIONAL_ID", "EMAIL_ADDRESS"]);
    expect(editor("ar", policy, edited)).toMatch(/data-entity="SA_IBAN"><input type="checkbox"\/>/);
    // The admin types ARABIC_NAME into the JSON: the checkbox follows.
    const typed = edited.replace('"EMAIL_ADDRESS"', '"EMAIL_ADDRESS", "ARABIC_NAME"');
    expect(editor("ar", policy, typed)).toMatch(/data-entity="ARABIC_NAME"><input type="checkbox" checked=""/);
    // Keywords typed in the JSON appear as tags.
    const words = configToText({ keywords: ["أولى", "ثانية"] });
    const tags = editor("ar", { checker: "keyword_list", checkpoints: ["INPUT"] }, words);
    expect(parseConfig(words).ok && readKeywords((parseConfig(words) as { config: Config }).config)).toEqual(["أولى", "ثانية"]);
    expect(tags).toContain("أولى");
    expect(tags).toContain("ثانية");
  });
});

describe("the readable configuration (ADR-0123)", () => {
  it("shows entities and targets by their Arabic labels, the raw JSON behind a toggle", () => {
    const policy = seeds().find((p) => p.id === "pii-redact-output")!;
    const html = renderIn(
      "ar",
      createElement(ConfigSummary, { checker: policy.checker, config: policy.config as Config, checkpoints: policy.checkpoints }),
    );
    expect(html).toContain("رقم هوية أو إقامة");
    expect(html).toContain("رقم جوال سعودي");
    expect(html).toContain(CATALOGS.ar.policies.targets.answer);
    expect(html).toContain(CATALOGS.ar.policies.targets.summary);
    expect(html).toContain(CATALOGS.ar.policies.summary.showTechnical);
    expect(html).toContain("عرض الإعدادات التقنية");
    expect(html).not.toContain("data-raw-config");
    expect(html).not.toContain('"entities"');
  });

  it("shows a judge's topic and examples, and a regex's patterns", () => {
    const judge = seeds().find((p) => p.id === "topics-politics-llm-judge")!;
    const config = judge.config as { topic_description: string; examples: { text: string }[] };
    const html = renderIn("ar", createElement(ConfigSummary, { checker: "llm_judge", config, checkpoints: judge.checkpoints }));
    expect(html).toContain(config.topic_description);
    expect(html.match(/data-example-row=/g)).toHaveLength(config.examples.length);
    expect(html).toContain(CATALOGS.ar.policies.config.minConfidence);
    const regex = seeds().find((p) => p.id === "tool-visibility-by-role")!;
    const regexHtml = renderIn("ar", createElement(ConfigSummary, { checker: "regex", config: regex.config as Config, checkpoints: regex.checkpoints }));
    expect(regexHtml).toContain('data-pattern="HIDDEN_CAPABILITY"');
    expect(regexHtml).toContain("^sql$");
  });
});

describe("scope and group fields (ADR-0123)", () => {
  it("splits a scope into kind and value and writes it back", () => {
    expect(parseScope("global")).toEqual({ kind: "global", value: "" });
    expect(parseScope("role:no-sql")).toEqual({ kind: "role", value: "no-sql" });
    expect(parseScope("tool:web")).toEqual({ kind: "tool", value: "web" });
    expect(scopeText("global", "ignored")).toBe("global");
    expect(scopeText("tenant", "default")).toBe("tenant:default");
    expect(scopeValueFor("tenant", null)).toBe("default");
    expect(scopeValueFor("role", ["policy:no-sql", "policy:pii-exempt"])).toBe("no-sql");
    expect(scopeValueFor("kb", null)).toBe("");
  });

  it("offers the five kinds in Arabic and the gateway's policy roles", () => {
    const html = renderIn(
      "ar",
      createElement(ScopeField, { scope: "role:pii-exempt", realmRoles: ["policy:no-sql", "policy:pii-exempt"], onChange: () => {} }),
    );
    for (const label of ["عام", "جهة", "دور", "قاعدة معرفة", "أداة"]) expect(html).toContain(`>${label}</option>`);
    expect(html).toContain('<option value="pii-exempt" selected="">pii-exempt</option>');
    expect(html).toContain('<option value="no-sql">no-sql</option>');
    const global = renderIn("ar", createElement(ScopeField, { scope: "global", realmRoles: [], onChange: () => {} }));
    expect(global).not.toContain(CATALOGS.ar.policies.fields.scopeValue);
  });

  it("hints are short Arabic sentences; each example id is its own bdi chip", () => {
    const hints = { ...CATALOGS.ar.policies.fields.scopeHints, group: CATALOGS.ar.policies.fields.groupHint };
    for (const [kind, text] of Object.entries(hints)) {
      expect(text, kind).not.toMatch(/[A-Za-z]/);
      expect(text.length, kind).toBeLessThan(120);
    }
    const html = renderIn("ar", createElement(ScopeField, { scope: "tool:web", realmRoles: [], onChange: () => {} }));
    const chips = [...html.matchAll(/<bdi dir="ltr"[^>]*data-example="">([^<]*)<\/bdi>/g)].map((m) => m[1]);
    expect(chips).toEqual(["web", "sql"]);
    const group = renderIn("ar", createElement(Hint, { text: CATALOGS.ar.policies.fields.groupHint, examples: ["pii-output", "pii-memory"] }));
    expect([...group.matchAll(/data-example="">([^<]*)</g)].map((m) => m[1])).toEqual(["pii-output", "pii-memory"]);
    expect(group).toMatch(/<span>[^<A-Za-z]+<\/span>/);
  });
});

describe("actor names in the history and change log (ADR-0123)", () => {
  it("shows the display name, with the username and id in a tooltip", () => {
    const html = renderIn("ar", createElement(ActorName, { id: "4f1c-9a", username: "admin", name: "مدير المنصة" }));
    expect(html).toBe('<bdi dir="auto" title="اسم المستخدم: admin، المعرّف: 4f1c-9a" data-actor="">مدير المنصة</bdi>');
  });

  it("shows the id of a row saved without a name", () => {
    const html = renderIn("en", createElement(ActorName, { id: "4f1c-9a" }));
    expect(html).toBe('<bdi dir="auto" title="Id: 4f1c-9a" data-actor="">4f1c-9a</bdi>');
  });
});

const view = (id: string, purpose: "production" | "test", name: string): PolicyView => ({
  policy: { id, version: 1, name_ar: name, name_en: name, purpose, scope: "global", checkpoints: ["INPUT"], checker: "keyword_list", action: "block" },
  created_at: "2026-10-11T00:00:00Z",
  created_by: "seed",
  updated_at: "2026-10-11T00:00:00Z",
  updated_by: "seed",
});

describe("truncated names (ADR-0123)", () => {
  it("a list name carries the full name in a tooltip", () => {
    const name = "سياسة باسم طويل جدًا لا يتسع له عمود القائمة في الصفحة";
    const html = renderIn(
      "ar",
      createElement(PolicyList, {
        policies: [view("long-name", "production", name)],
        selected: null,
        busy: null,
        showTest: false,
        onShowTest: () => {},
        onOpen: () => {},
        onToggle: () => {},
      }),
    );
    expect(html).toContain(`class="block truncate font-medium" title="${name}">${name}</span>`);
  });
});

describe("the daily matches table follows the test-policy toggle (ADR-0123)", () => {
  const policies = [view("prompt-injection-input", "production", "حقن"), view("smoke-live-block", "test", "فحص")];
  const today = new Date().toISOString().slice(0, 10);
  const rows = [
    { day: today, policy_id: "prompt-injection-input", action: "block", matches: 2 },
    { day: today, policy_id: "smoke-live-block", action: "block", matches: 5 },
  ];

  it("hides test policies until the toggle is on", () => {
    expect(visibleMatchRows(rows, policies, false).map((r) => r.policy_id)).toEqual(["prompt-injection-input"]);
    expect(visibleMatchRows(rows, policies, true)).toHaveLength(2);
    const metrics = { days: 14, rows };
    const hidden = renderIn("ar", createElement(MatchesPerDay, { metrics, policies, showTest: false }));
    expect(hidden).toContain('data-match-row="prompt-injection-input"');
    expect(hidden).not.toContain('data-match-row="smoke-live-block"');
    const shown = renderIn("ar", createElement(MatchesPerDay, { metrics, policies, showTest: true }));
    expect(shown).toContain('data-match-row="smoke-live-block"');
  });
});

describe("test panel labels (ADR-0123)", () => {
  it("names the resulting text after the action", () => {
    const expected = { redact: "النص بعد الإخفاء", block: "النص بعد الحظر", warn: "النص بعد التحذير" };
    for (const [action, text] of Object.entries(expected)) {
      expect(renderIn("ar", createElement(OutputHeading, { action }))).toContain(`>${text}</h3>`);
    }
    for (const action of Object.keys(CATALOGS.ar.guardActions)) {
      expect(CATALOGS.ar.policies.test.outputBy).toHaveProperty(action);
      expect(CATALOGS.en.policies.test.outputBy).toHaveProperty(action);
    }
  });

  it("names entities by the presidio editor's Arabic labels, each value in its own bdi", () => {
    const html = renderIn(
      "ar",
      createElement(Explanation, { explanationKey: "entities", args: { items: "SA_MOBILE\nSA_NATIONAL_ID" }, fallback: "entities: SA_MOBILE, SA_NATIONAL_ID" }),
    );
    expect(html).toContain("كيانات وُجدت:");
    expect(html).toMatch(/<bdi[^>]*data-entity="SA_MOBILE">رقم جوال سعودي<\/bdi>/);
    expect(html).toMatch(/<bdi[^>]*data-entity="SA_NATIONAL_ID">رقم هوية أو إقامة<\/bdi>/);
    expect(html).not.toContain(">SA_MOBILE<");
  });

  it("puts each technical value of a sentence in its own bdi", () => {
    const html = renderIn(
      "ar",
      createElement(Explanation, { explanationKey: "above_ceiling", args: { tier: "heavy", ceiling: "standard" }, fallback: "" }),
    );
    expect([...html.matchAll(/<bdi dir="auto" data-value="">([^<]*)<\/bdi>/g)].map((m) => m[1])).toEqual(["heavy", "standard"]);
    const patterns = renderIn("ar", createElement(Explanation, { explanationKey: "patterns", args: { items: "OVERRIDE_AR\nROLE_AR" } }));
    expect([...patterns.matchAll(/data-value="">([^<]*)</g)].map((m) => m[1])).toEqual(["OVERRIDE_AR", "ROLE_AR"]);
    for (const text of Object.values(CATALOGS.ar.policies.explanation)) expect(text).not.toMatch(/\{items\}/);
  });
});

describe("the import field (ADR-0123)", () => {
  it("has an Arabic-only placeholder; the format name is the heading chip", () => {
    expect(CATALOGS.ar.policies.io.placeholder).toBe("الصق هنا ملف السياسات");
    expect(CATALOGS.ar.policies.io.placeholder).not.toMatch(/[A-Za-z]/);
    expect(CATALOGS.ar.policies.io.format).toBe("YAML");
  });
});
