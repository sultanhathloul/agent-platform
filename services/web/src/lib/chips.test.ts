import { describe, expect, it } from "vitest";
import { shownChips } from "./chips";
import type { Chip } from "./stream";

const search = (n: number, status = "ok"): Chip => ({
  kind: "tool", key: `t${n}`, callId: `c${n}`, tool: "kb_search", capability: "kb", status, latencyMs: 100,
  label: { ar: "بحث في قواعد المعرفة", en: "Searching the knowledge bases" },
});
const tier = (key: string, value: string): Chip => ({ kind: "tier", key, role: "report_writer", step: null, tier: value, reason: "" });
const guard = (key: string, action: string): Chip => ({
  kind: "guard", key, checkpoint: "POST_TOOL", policyId: "p", policyVersion: 1, policyName: null, action, target: null,
});

describe("shownChips (ADR-0416)", () => {
  it("collapses a tool's calls into one chip with their count and worst status", () => {
    const shown = shownChips([...Array.from({ length: 8 }, (_, i) => search(i)), search(9, "timeout")], { admin: false });
    expect(shown).toHaveLength(1);
    expect(shown[0]).toMatchObject({ kind: "tool", tool: "kb_search", count: 9, status: "timeout", latencyMs: 900 });
  });

  it("never shows the same tier twice", () => {
    const shown = shownChips([tier("a", "fast"), tier("b", "heavy"), tier("c", "heavy"), tier("d", "fast")], { admin: false });
    expect(shown.map((c) => (c.kind === "tier" ? c.tier : ""))).toEqual(["fast", "heavy"]);
  });

  it("shows log-only and allow guard chips to administrators only", () => {
    const chips = [guard("g1", "log_only"), guard("g2", "allow"), guard("g3", "redact")];
    expect(shownChips(chips, { admin: false }).map((c) => c.key)).toEqual(["g3"]);
    expect(shownChips(chips, { admin: true }).map((c) => c.key)).toEqual(["g1", "g2", "g3"]);
  });
});
