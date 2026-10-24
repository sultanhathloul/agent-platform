---
slug: official_letter
version: 1.0.0
name: {ar: "خطاب رسمي", en: "Official letter"}
description:
  ar: "يكتب الإجابة خطابًا رسميًا بين جهتين: الرقم والتاريخ والمرفقات، مخاطبة صاحب السعادة، التحية، النص، عبارة الختام، وكتلة التوقيع."
  en: "Writes the answer as an official letter between two bodies: reference, date, attachments, the address to His Excellency, the greeting, the body, the closing and the signature block."
when_to_use:
  ar: "عندما يطلب المستخدم أن يُكتب له خطاب أو كتاب رسمي أو مخاطبة إلى جهة أخرى، لا عندما يسأل عن خطاب ألقاه أحد أو عن معنى الكلمة."
  en: "When the user asks for an official letter to another body to be written for them, not when they ask about a speech someone gave or what the word means."
permission: skill:official_letter
requires: {capabilities: []}
routing_hints:
  keywords:
    ar: ["خطاب رسمي", "خطاب", "مخاطبة", "كتاب رسمي"]
    en: ["official letter", "letter to"]
  rules: [{when: keyword_candidate}]
max_instruction_tokens: 900
---
Notes for reviewers (never loaded into a prompt):

- A reference layout (phase 06, ADR-0302) for a letter from one government body to another; the
  entity's approved letter template replaces it later, by PR, with its evals (README §20).
- The skill shapes the form only (ADR-0289): facts come from the capability's findings or the
  user's message; a field the user did not give is a bracketed placeholder, never invented.
- Composes with any capability (`requires.capabilities: []`).
- This folder landed in its own commit touching only the paths of ADR-0305 (zero core change).
