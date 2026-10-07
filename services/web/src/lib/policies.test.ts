import { describe, expect, it } from "vitest";
import {
  emptyForm,
  explanationArgs,
  formatList,
  formFromPolicy,
  highlight,
  lastDays,
  matchesByPolicy,
  messageFor,
  parsePolicyRoles,
  policyFromForm,
  unknownPolicyRole,
} from "./policies";

describe("highlight", () => {
  it("marks the matched Arabic span by character offsets", () => {
    const text = "اتصل على ٠٥٠١٢٣٤٥٦٧ اليوم";
    const start = text.indexOf("٠");
    expect(highlight(text, [{ start, end: start + 10, entity: "SA_MOBILE" }])).toEqual([
      { text: "اتصل على ", entity: null },
      { text: "٠٥٠١٢٣٤٥٦٧", entity: "SA_MOBILE" },
      { text: " اليوم", entity: null },
    ]);
  });

  it("merges overlapping spans, keeps order and clamps spans outside the text", () => {
    const segments = highlight("abcdefgh", [
      { start: 5, end: 99, entity: "C" },
      { start: 0, end: 3, entity: "A" },
      { start: 2, end: 4, entity: "B" },
    ]);
    expect(segments).toEqual([
      { text: "abcd", entity: "A" },
      { text: "e", entity: null },
      { text: "fgh", entity: "C" },
    ]);
    expect(segments.map((s) => s.text).join("")).toBe("abcdefgh");
  });

  it("returns the whole text when nothing matched", () => {
    expect(highlight("نص سليم", [])).toEqual([{ text: "نص سليم", entity: null }]);
  });
});

describe("policy forms", () => {
  it("round-trips a policy through the editor form", () => {
    const policy = {
      id: "pii-redact-output",
      version: 3,
      name: "حجب البيانات",
      description: "",
      enabled: true,
      scope: "global",
      checkpoints: ["DATA"],
      checker: "presidio",
      config: { entities: ["SA_MOBILE"], targets: ["answer"] },
      action: "redact",
      severity: "high",
      group: "pii-output",
    };
    const parsed = policyFromForm(formFromPolicy(policy));
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.policy.config).toEqual(policy.config);
    expect(parsed.policy.version).toBeUndefined();
    expect(parsed.policy.message_ar).toBeUndefined();
    expect(parsed.policy.group).toBe("pii-output");
  });

  it("refuses config that is not a JSON object", () => {
    expect(policyFromForm({ ...emptyForm(), configText: "{oops" })).toEqual({ ok: false, error: "config_json" });
    expect(policyFromForm({ ...emptyForm(), configText: "[1]" })).toEqual({ ok: false, error: "config_json" });
  });
});

describe("matches per day", () => {
  it("groups rows by policy with totals per action and day, busiest first", () => {
    const grouped = matchesByPolicy([
      { day: "2026-10-05", policy_id: "pii-redact-output", action: "redact", matches: 2 },
      { day: "2026-10-06", policy_id: "pii-redact-output", action: "redact", matches: 3 },
      { day: "2026-10-06", policy_id: "prompt-injection-input", action: "block", matches: 1 },
    ]);
    expect(grouped.map((g) => [g.policyId, g.total])).toEqual([
      ["pii-redact-output", 5],
      ["prompt-injection-input", 1],
    ]);
    expect(grouped[0]?.byDay).toEqual({ "2026-10-05": 2, "2026-10-06": 3 });
  });

  it("lists the last days oldest first in UTC", () => {
    expect(lastDays(3, new Date(Date.UTC(2026, 9, 1, 23)))).toEqual(["2026-09-29", "2026-09-30", "2026-10-01"]);
  });
});

describe("role scopes", () => {
  it("warns about a role scope whose policy role the realm does not hold", () => {
    const roles = ["policy:pii-exempt", "policy:no-sql"];
    expect(unknownPolicyRole("role:pii-exempt", roles)).toBeNull();
    expect(unknownPolicyRole("role:typo", roles)).toBe("policy:typo");
    expect(unknownPolicyRole("global", roles)).toBeNull();
    expect(unknownPolicyRole("role:typo", null)).toBeNull(); // roles not loaded yet: no warning
  });
});

describe("locale-aware texts (rule 7)", () => {
  it("joins a list with the locale's separator", () => {
    expect(formatList(["pii-redact-output@1", "names@2"], "ar")).toBe("pii-redact-output@1، names@2");
    expect(formatList(["pii-redact-output@1", "names@2"], "en")).toBe("pii-redact-output@1, names@2");
  });

  it("shows the policy message in the reader's locale, the other only when missing", () => {
    const both = { message_ar: "محجوب", message_en: "Blocked" };
    expect(messageFor(both, "ar")).toBe("محجوب");
    expect(messageFor(both, "en")).toBe("Blocked");
    expect(messageFor({ message_ar: "محجوب", message_en: null }, "en")).toBe("محجوب");
    expect(messageFor({}, "en")).toBeNull();
  });

  it("joins explanation items per locale and keeps other arguments", () => {
    const args = { items: "EMAIL_ADDRESS\nSA_MOBILE", confidence: "0.90" };
    expect(explanationArgs(args, "ar")).toEqual({ items: "EMAIL_ADDRESS، SA_MOBILE", confidence: "0.90" });
    expect(explanationArgs(undefined, "en")).toEqual({});
  });

  it("parses policy roles typed with spaces or commas", () => {
    expect(parsePolicyRoles(" policy:b, policy:a policy:b ")).toEqual(["policy:a", "policy:b"]);
  });
});
