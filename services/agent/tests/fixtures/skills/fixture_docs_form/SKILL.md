---
slug: fixture_docs_form
version: 2.0.0
name: {ar: "ملخص الملف", en: "File summary form"}
description: {ar: "يرتب ما في الملف في ملخص", en: "Lays out what the file says as a summary"}
when_to_use: {ar: "عندما يطلب المستخدم ملخص الملف", en: "When the user asks for the file summary form"}
permission: "skill:fixture_docs_form"
requires: {capabilities: [docs]}
routing_hints:
  keywords: {ar: ["ملخص الملف"], en: ["file summary form"]}
  rules: [{when: keyword_candidate}]
max_instruction_tokens: 300
---
Test-only skill that requires the docs capability (tests/fixtures/skills).
