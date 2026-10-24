---
slug: fixture_form
version: 1.0.0
name: {ar: "نموذج الاختبار", en: "Test form"}
description: {ar: "يرتب الإجابة في نموذج اختبار من ثلاثة أقسام", en: "Lays the answer out as a three-part test form"}
when_to_use: {ar: "عندما يطلب المستخدم نموذج الاختبار", en: "When the user asks for the test form"}
permission: "skill:fixture_form"
requires: {capabilities: []}
routing_hints:
  keywords: {ar: ["نموذج الاختبار"], en: ["test form"]}
  rules: [{when: keyword_candidate}]
max_instruction_tokens: 300
---
Test-only skill (tests/fixtures/skills). No core file names it (G5).
