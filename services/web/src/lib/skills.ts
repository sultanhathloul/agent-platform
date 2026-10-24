// The composer's skill picker (phase 06, ADR-0292): pure, so it is tested without a browser. The
// list is the caller's own (GET /v1/skills); nothing is picked by default, the pick goes with one
// turn and is cleared after it, and nothing asks the user to confirm.
import type { ChatTurnRequest, SkillView } from "@/lib/api/types";

/** Nothing picked: the skill is chosen automatically, or none. */
export const NO_SKILL = "";

/** The picked slug when it is still one of the caller's skills; otherwise none. */
export function pickedSkill(selected: string, skills: SkillView[]): string | undefined {
  return selected && skills.some((s) => s.slug === selected) ? selected : undefined;
}

/** The turn's body with the picked skill, or without the field when none is picked. */
export function withSkill(body: ChatTurnRequest, selected: string, skills: SkillView[]): ChatTurnRequest {
  const skill = pickedSkill(selected, skills);
  return skill ? { ...body, skill } : body;
}

/** A skill's name in the page's language. */
export function skillName(label: { ar: string; en: string }, locale: string): string {
  return locale === "ar" ? label.ar : label.en;
}
