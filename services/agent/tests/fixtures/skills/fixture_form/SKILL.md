---
# Test-only skill (tests/fixtures/skills). No core file names it (G5).
name: fixture_form
description: Lays the answer out as a three-part test form. Use when the user asks for the test form.
license: Complete terms in LICENSE.txt
sovereign:
  version: 1.0.0
  name: {ar: "نموذج الاختبار", en: "Test form"}
  description: {ar: "يرتب الإجابة في نموذج اختبار من ثلاثة أقسام، عندما يطلب المستخدم نموذج الاختبار"}
  requires: {capabilities: []}
  routing_hints:
    keywords: {ar: ["نموذج الاختبار"], en: ["test form"]}
    rules: [{when: keyword_candidate}]
---
FIXTURE-SKILL-INSTRUCTIONS fixture_form: اكتب الإجابة في ثلاثة أقسام: العنوان، المضمون، الخاتمة.
Follow the layout in `reference/layout.md`.
