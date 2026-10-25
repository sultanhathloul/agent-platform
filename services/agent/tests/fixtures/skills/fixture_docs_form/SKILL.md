---
# Test-only skill that requires the docs capability (tests/fixtures/skills).
name: fixture_docs_form
description: Lays out what the file says as a summary. Use when the user asks for the file summary form.
sovereign:
  version: 2.0.0
  name: {ar: "ملخص الملف", en: "File summary form"}
  description: {ar: "يرتب ما في الملف في ملخص، عندما يطلب المستخدم ملخص الملف"}
  requires: {capabilities: [docs]}
  routing_hints:
    keywords: {ar: ["ملخص الملف"], en: ["file summary form"]}
    rules: [{when: keyword_candidate}]
---
FIXTURE-SKILL-INSTRUCTIONS fixture_docs_form: اكتب الإجابة في ثلاثة أقسام: العنوان، المضمون، الخاتمة.
