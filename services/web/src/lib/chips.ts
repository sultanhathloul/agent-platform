import type { Chip } from "./stream";

/**
 * The chips a reply shows (owner visual check on PR #17, ADR-0416): one chip per tool with how many times it
 * ran (nine KB searches read «بحث في قواعد المعرفة × 9»), its status the worst of its calls; one chip per
 * tier; a guard chip whose action the reader does not see the effect of (allow, log_only, e.g. POST_TOOL
 * log-only) only for an administrator.
 */
export type ShownChip = Chip & { count: number };

const QUIET_GUARD_ACTIONS = new Set(["allow", "log_only"]);
const STATUS_ORDER = ["ok", "running", "timeout", "error", "failed", "refused", "blocked"];

function worse(a: string, b: string): string {
  return STATUS_ORDER.indexOf(b) > STATUS_ORDER.indexOf(a) ? b : a;
}

export function shownChips(chips: Chip[], { admin }: { admin: boolean }): ShownChip[] {
  const out: ShownChip[] = [];
  const tools = new Map<string, ShownChip & { kind: "tool" }>();
  const tiers = new Set<string>();
  for (const chip of chips) {
    if (chip.kind === "tier") {
      if (tiers.has(chip.tier)) continue;
      tiers.add(chip.tier);
      out.push({ ...chip, count: 1 });
    } else if (chip.kind === "guard") {
      if (!admin && QUIET_GUARD_ACTIONS.has(chip.action)) continue;
      out.push({ ...chip, count: 1 });
    } else {
      const seen = tools.get(chip.tool);
      if (seen) {
        seen.count += 1;
        seen.status = worse(seen.status, chip.status);
        seen.latencyMs = seen.latencyMs !== null && chip.latencyMs !== null ? seen.latencyMs + chip.latencyMs : null;
        continue;
      }
      const shown = { ...chip, count: 1 };
      tools.set(chip.tool, shown);
      out.push(shown);
    }
  }
  return out;
}
