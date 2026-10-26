# Notes for reviewers

Not loaded into a prompt: SKILL.md does not name this file (ADR-0318).

- A reference layout (phase 06, ADR-0302). The entity's approved memo template replaces it later,
  by PR, with its evals (README §20).
- The skill shapes the form only (ADR-0289): every fact comes from the capability's findings or the
  user's message. What the user did not give is a bracketed placeholder, never an invented number,
  date, name or title.
- Composes with any capability (`requires.capabilities: []`): general, docs, directions.
- Instructions: the body of SKILL.md, version 1.1.0 (`sovereign.version`). It is the Arabic text
  of 1.1.0, whose last rule covers a request written in English; the English variant of the
  former `prompts/instructions.en.md.j2` is not carried over (ADR-0319).
- Evals: evals/official_memo.jsonl with evals/metrics.yaml; thresholds in evals/thresholds.yaml.
