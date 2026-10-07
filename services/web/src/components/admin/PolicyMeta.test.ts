import { createElement, type ReactElement } from "react";
import { describe, expect, it } from "vitest";
import type { PolicyView } from "@/lib/api/types";
import { ACTIONS } from "@/lib/policies";
import { CATALOGS, renderIn, type TestLocale } from "@/test/render";
import { PolicyList } from "./PolicyList";
import { PolicyMeta } from "./PolicyMeta";

function render(locale: TestLocale, element: ReactElement): string {
  return renderIn(locale, element);
}

/** Each chip of the meta line as [name, tag, text], in document order. */
function chips(html: string): [string, string, string][] {
  return [...html.matchAll(/<(\w+)[^>]*data-chip="(\w+)"[^>]*>([^<]*)<\/\1>/g)].map((m) => [m[2]!, m[1]!, m[3]!]);
}

const POLICY = {
  id: "pii-redact-output",
  version: 3,
  action: "redact",
  scope: "global",
  checkpoints: ["OUTPUT", "DATA"],
  checker: "presidio",
};

describe("the policy meta line (ADR-0119)", () => {
  for (const locale of ["ar", "en"] as const) {
    it(`renders its chips in the fixed order, each in a bdi, in ${locale}`, () => {
      const html = render(locale, createElement(PolicyMeta, { policy: POLICY, exception: true, overrides: ["pii-wide@1"] }));
      expect(chips(html)).toEqual([
        ["action", "bdi", CATALOGS[locale].guardActions.redact],
        ["exception", "bdi", CATALOGS[locale].policies.overrides.badge],
        ["scope", "bdi", "global"],
        ["checkpoints", "bdi", "OUTPUT, DATA"],
        ["checker", "bdi", "presidio"],
        ["ref", "bdi", "pii-redact-output@3"],
      ]);
      expect(html).not.toContain("·");
      expect(html).not.toContain("،");
    });

    it(`leaves the exception badge out when the policy is not one, in ${locale}`, () => {
      const html = render(locale, createElement(PolicyMeta, { policy: POLICY }));
      expect(chips(html).map(([name]) => name)).toEqual(["action", "scope", "checkpoints", "checker", "ref"]);
    });
  }

  it("takes every action label from the locale's catalog", () => {
    expect(CATALOGS.ar.guardActions).toEqual({
      allow: "سماح",
      block: "حظر",
      redact: "إخفاء",
      rewrite: "إعادة صياغة",
      warn: "تحذير",
      require_approval: "طلب موافقة",
      log_only: "تسجيل فقط",
    });
    for (const locale of ["ar", "en"] as const) {
      for (const action of ACTIONS) {
        const [first] = chips(render(locale, createElement(PolicyMeta, { policy: { ...POLICY, action } })));
        expect(first).toEqual(["action", "bdi", CATALOGS[locale].guardActions[action]]);
      }
    }
  });

  it("puts no monospace face on a chip that holds Arabic", () => {
    const html = render("ar", createElement(PolicyMeta, { policy: POLICY, exception: true }));
    for (const name of ["action", "exception"]) {
      const tag = html.match(new RegExp(`<bdi[^>]*data-chip="${name}"[^>]*>`))?.[0] ?? "";
      expect(tag, name).not.toContain("font-mono");
    }
  });
});

describe("the policy list (ADR-0119)", () => {
  const view = (id: string, purpose: "production" | "test", name_ar: string, name_en: string): PolicyView => ({
    policy: { id, version: 1, name_ar, name_en, purpose, scope: "global", checkpoints: ["INPUT"], checker: "keyword_list", action: "block" },
    created_at: "2026-10-07T00:00:00Z",
    created_by: "seed",
    updated_at: "2026-10-07T00:00:00Z",
    updated_by: "seed",
  });
  const policies = [
    view("prompt-injection-input", "production", "حظر حقن التعليمات", "Block prompt injection"),
    view("smoke-live-block", "test", "الفحص الشامل: حظر كلمة", "Smoke: live keyword block"),
  ];
  const props = { policies, selected: null, busy: null, onOpen: () => {}, onToggle: () => {} };

  for (const locale of ["ar", "en"] as const) {
    it(`hides test policies until the toggle is on and names policies in ${locale}`, () => {
      const html = render(locale, createElement(PolicyList, props));
      expect(html).toContain('data-policy-id="prompt-injection-input"');
      expect(html).not.toContain('data-policy-id="smoke-live-block"');
      expect(html).toContain(CATALOGS[locale].policies.showTest);
      const toggle = html.match(/<label[^>]*><input type="checkbox"[^>]*\/>/)?.[0] ?? "";
      expect(toggle).not.toContain("checked");
      expect(html).toContain(locale === "ar" ? "حظر حقن التعليمات" : "Block prompt injection");
      expect(html).not.toContain(locale === "ar" ? "Block prompt injection" : "حظر حقن التعليمات");
    });
  }
});
