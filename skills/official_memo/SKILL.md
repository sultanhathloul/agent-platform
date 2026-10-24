---
slug: official_memo
version: 1.0.0
name: {ar: "مذكرة داخلية", en: "Internal memo"}
description:
  ar: "يكتب الإجابة مذكرة داخلية رسمية بين إدارات الجهة: الترويسة، الرقم والتاريخ والمرفقات، إلى ومن والموضوع، التحية، النص، الختام، وكتلة التوقيع."
  en: "Writes the answer as an official internal memo between a body's departments: header, reference, date, attachments, to, from, subject, greeting, body, closing and signature block."
when_to_use:
  ar: "عندما يطلب المستخدم أن تُكتب له مذكرة أو مذكرة داخلية، لا عندما يسأل عن معنى الكلمة أو عن مذكرة كتبها غيره."
  en: "When the user asks for a memo or an internal memorandum to be written for them, not when they ask what the word means or about a memo someone else wrote."
permission: skill:official_memo
requires: {capabilities: []}
routing_hints:
  keywords:
    ar: ["مذكرة", "مذكره", "مذكرة داخلية"]
    en: ["memo", "memorandum"]
  rules: [{when: keyword_candidate}]
max_instruction_tokens: 900
---
Notes for reviewers (never loaded into a prompt):

- A reference layout (phase 06, ADR-0302). The entity's approved memo template replaces it later,
  by PR, with its evals (README §20).
- The skill shapes the form only (ADR-0289): every fact comes from the capability's findings or the
  user's message. What the user did not give is a bracketed placeholder, never an invented number,
  date, name or title.
- Composes with any capability (`requires.capabilities: []`): general, docs, directions.
- Instructions: prompts/instructions.{ar,en}.md.j2, id `official_memo.instructions`.
- Evals: evals/official_memo.jsonl with evals/metrics.yaml; thresholds in evals/thresholds.yaml.
