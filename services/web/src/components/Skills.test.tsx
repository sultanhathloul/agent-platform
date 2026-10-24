import { describe, expect, it } from "vitest";
import type { ChatTurnRequest, SkillView } from "@/lib/api/types";
import { turnsFromHistory } from "@/lib/conversations";
import { NO_SKILL, pickedSkill, withSkill } from "@/lib/skills";
import { applyEvent, initialAssistant } from "@/lib/stream";
import { renderIn } from "@/test/render";
import { AssistantMessageView } from "./AssistantMessageView";
import { SkillPicker } from "./SkillPicker";

// Test-only slugs and labels: no web file names a shipped skill (phase 06, G5).
const SKILLS: SkillView[] = [
  { slug: "fixture_form", name: { ar: "نموذج الاختبار", en: "Test form" }, description: { ar: "وصف النموذج", en: "The form" } },
  { slug: "fixture_note", name: { ar: "ملاحظة الاختبار", en: "Test note" }, description: { ar: "وصف الملاحظة", en: "The note" } },
];
const BODY: ChatTurnRequest = { conversation_id: "c", message: "س", selected_kbs: [], attachment_ids: [], locale: "ar" };

function text(html: string): string {
  return html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}

describe("the skill picker (D4)", () => {
  it("lists only the caller's skills, with nothing picked by default", () => {
    const html = renderIn("ar", <SkillPicker skills={SKILLS} value={NO_SKILL} onChange={() => {}} />);
    const options = [...html.matchAll(/<option[^>]*value="([^"]*)"[^>]*>([^<]*)<\/option>/g)].map((m) => [m[1], m[2]]);
    expect(options).toEqual([["", "تلقائي"], ["fixture_form", "نموذج الاختبار"], ["fixture_note", "ملاحظة الاختبار"]]);
    expect(html).toContain('<option value="" selected="">');
    expect(text(html)).toContain("نموذج الإجابة");
    expect(renderIn("en", <SkillPicker skills={SKILLS} value={NO_SKILL} onChange={() => {}} />)).toContain("Test form");
  });

  it("is not shown to a caller with no skill", () => {
    expect(renderIn("ar", <SkillPicker skills={[]} value={NO_SKILL} onChange={() => {}} />)).toBe("");
  });

  it("shows the pick, and falls back to automatic for a skill no longer listed", () => {
    expect(renderIn("ar", <SkillPicker skills={SKILLS} value="fixture_note" onChange={() => {}} />)).toContain(
      '<option value="fixture_note" title="وصف الملاحظة" selected="">',
    );
    expect(renderIn("ar", <SkillPicker skills={SKILLS} value="gone_skill" onChange={() => {}} />)).toContain(
      '<option value="" selected="">',
    );
  });

  it("sends the pick with the turn, only when it is one of the caller's skills", () => {
    expect(withSkill(BODY, "fixture_form", SKILLS)).toEqual({ ...BODY, skill: "fixture_form" });
    expect(withSkill(BODY, NO_SKILL, SKILLS)).toEqual(BODY);
    expect("skill" in withSkill(BODY, "gone_skill", SKILLS)).toBe(false);
    expect(pickedSkill("fixture_form", [])).toBeUndefined();
  });
});

describe("the reply's skill chip", () => {
  it("comes from the skill event, before any token", () => {
    const event = {
      type: "skill",
      request_id: "r",
      turn_id: "t",
      seq: 2,
      data: { slug: "fixture_form", label: { ar: "نموذج الاختبار", en: "Test form" } },
    } as const;
    const message = applyEvent(initialAssistant(), event);
    expect(message.skill).toEqual({ slug: "fixture_form", label: { ar: "نموذج الاختبار", en: "Test form" } });
    expect(message.text).toBe("");
    const ar = renderIn("ar", <AssistantMessageView message={{ ...message, status: "done", text: "نص" }} />);
    expect(text(ar)).toContain("مكتوبة بنموذج نموذج الاختبار");
    const en = renderIn("en", <AssistantMessageView message={{ ...message, status: "done", text: "text" }} />);
    expect(text(en)).toContain("Written as Test form");
  });

  it("comes from the history, and a reply without a skill shows none", () => {
    const turns = turnsFromHistory([
      { role: "user", text: "س", attachments: [], citations: [] },
      { role: "assistant", text: "ج", turn_id: "t1", attachments: [], citations: [],
        skill: { slug: "fixture_form", label: { ar: "نموذج الاختبار", en: "Test form" } } },
      { role: "user", text: "س٢", attachments: [], citations: [] },
      { role: "assistant", text: "ج٢", turn_id: "t2", attachments: [], citations: [] },
    ]);
    const [withOne, without] = turns.map((turn) => turn.answer);
    expect(withOne?.skill?.slug).toBe("fixture_form");
    expect(without?.skill).toBeUndefined();
    expect(renderIn("ar", <AssistantMessageView message={without!} />)).not.toContain("data-skill");
    expect(renderIn("ar", <AssistantMessageView message={withOne!} />)).toContain('data-skill="fixture_form"');
  });

  it("writes its Arabic without Latin letters", () => {
    const html = renderIn("ar", <SkillPicker skills={SKILLS} value={NO_SKILL} onChange={() => {}} />);
    expect(/[A-Za-z]/.test(text(html))).toBe(false);
  });
});
