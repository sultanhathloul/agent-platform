// The structured configuration editors of the admin policy form (ADR-0123). Pure helpers: each
// reads a field out of a checker's config and writes it back into a copy, leaving every other key
// as it was, so loading a config and saving it unchanged gives the identical config. The engine's
// validator (the agent) stays the authority; `configProblems` only refuses what it would refuse.

export type Config = Record<string, unknown>;

/** The entities of infra/presidio/recognizers.yaml, in the order the editor lists them. */
export const PRESIDIO_ENTITIES = ["SA_MOBILE", "SA_NATIONAL_ID", "SA_IBAN", "EMAIL_ADDRESS", "ARABIC_NAME"] as const;
/** Model tiers and roles of config/models.yaml, lowest tier first (README §19.1). */
export const MODEL_TIERS = ["fast", "standard", "heavy"] as const;
export const MODEL_ROLES = ["router", "summarizer", "judge", "extractor", "reflection", "reasoning"] as const;
export const DATA_TARGETS = ["answer", "summary", "memory_candidate"] as const;

/** The engine's defaults (app/guardrails/schema.py), shown when a config leaves them out. */
export const DEFAULT_SCORE_THRESHOLD = 0.5;
export const DEFAULT_MIN_CONFIDENCE = 0.5;

export type RegexRow = { name: string; pattern: string } & Config;
export type JudgeExample = { text: string; violates: boolean };

/** A new checker's starting config; DATA targets carry over from the config being replaced. */
export function starterConfig(checker: string, previous: Config = {}): Config {
  const base: Record<string, Config> = {
    keyword_list: { keywords: [] },
    regex: { patterns: [] },
    presidio: { entities: [] },
    llm_judge: { topic_description: "", examples: [] },
    tier_ceiling: { max_tier: "standard" },
  };
  const config = { ...(base[checker] ?? {}) };
  if (Array.isArray(previous.targets)) config.targets = previous.targets;
  return config;
}

export type ParsedConfig = { ok: true; config: Config } | { ok: false };

/** The advanced view's text as a config: a JSON object, or not valid. */
export function parseConfig(text: string): ParsedConfig {
  try {
    const value: unknown = JSON.parse(text.trim() ? text : "{}");
    if (typeof value === "object" && value !== null && !Array.isArray(value)) return { ok: true, config: value as Config };
  } catch {
    // falls through
  }
  return { ok: false };
}

/** The advanced view's text for a config the editor changed. */
export function configToText(config: Config): string {
  return JSON.stringify(config, null, 2);
}

/** `config` with `key` set to `value`, or without `key` when `value` is undefined. */
export function withField(config: Config, key: string, value: unknown): Config {
  const next = { ...config };
  if (value === undefined) delete next[key];
  else next[key] = value;
  return next;
}

const strings = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
const numberOr = (value: unknown): number | undefined => (typeof value === "number" ? value : undefined);

export const readKeywords = (config: Config): string[] => strings(config.keywords);
/** Keywords as tags: trimmed, empty ones dropped, each one once. */
export function addKeywords(current: string[], typed: string): string[] {
  const out = [...current];
  for (const word of typed.split(/[\n،,]+/).map((w) => w.trim())) {
    if (word && !out.includes(word)) out.push(word);
  }
  return out;
}

export function readPatterns(config: Config): RegexRow[] {
  return Array.isArray(config.patterns)
    ? config.patterns
        .filter((p): p is Config => typeof p === "object" && p !== null)
        .map((p) => ({ ...p, name: typeof p.name === "string" ? p.name : "", pattern: typeof p.pattern === "string" ? p.pattern : "" }))
    : [];
}

export const readEntities = (config: Config): string[] => strings(config.entities);
/** The entities with `entity` switched on or off, in the editor's order; unknown ones are kept. */
export function toggleEntity(current: string[], entity: string, on: boolean): string[] {
  const set = new Set(current);
  if (on) set.add(entity);
  else set.delete(entity);
  const known = PRESIDIO_ENTITIES.filter((e) => set.has(e));
  const unknown = current.filter((e) => !(PRESIDIO_ENTITIES as readonly string[]).includes(e) && set.has(e));
  return [...known, ...unknown];
}

export const readScoreThreshold = (config: Config): number | undefined => numberOr(config.score_threshold);
export const readMinConfidence = (config: Config): number | undefined => numberOr(config.min_confidence);
export const readTopic = (config: Config): string => (typeof config.topic_description === "string" ? config.topic_description : "");

export function readExamples(config: Config): JudgeExample[] {
  return Array.isArray(config.examples)
    ? config.examples
        .filter((e): e is Config => typeof e === "object" && e !== null)
        .map((e) => ({ text: typeof e.text === "string" ? e.text : "", violates: e.violates === true }))
    : [];
}

export const readMaxTier = (config: Config): string => (typeof config.max_tier === "string" ? config.max_tier : "");
/** The model roles a ceiling applies to; undefined means every role. */
export const readRoles = (config: Config): string[] | undefined => (Array.isArray(config.roles) ? strings(config.roles) : undefined);
/** The DATA targets a policy guards; undefined means every target. */
export const readTargets = (config: Config): string[] | undefined =>
  Array.isArray(config.targets) ? strings(config.targets) : undefined;

/** A list switched by checkbox, where undefined stands for "all of `all`". */
export function toggleIn(current: string[] | undefined, all: readonly string[], item: string, on: boolean): string[] {
  const set = new Set(current ?? all);
  if (on) set.add(item);
  else set.delete(item);
  return all.filter((x) => set.has(x)).concat((current ?? []).filter((x) => !all.includes(x)));
}

/**
 * A Python pattern compiled by the browser's engine, to catch a broken pattern before saving.
 * Python's named groups and backreferences are rewritten to JavaScript's; a pattern only Python
 * understands is still left to the agent's validator, which answers with its reason.
 */
export function patternCompiles(pattern: string): boolean {
  if (!pattern) return false;
  const js = pattern.replace(/\(\?P</g, "(?<").replace(/\(\?P=(\w+)\)/g, "\\k<$1>");
  try {
    new RegExp(js, "u");
    return true;
  } catch {
    try {
      new RegExp(js);
      return true;
    } catch {
      return false;
    }
  }
}

const REGEX_NAME = /^[A-Za-z0-9_]{1,64}$/;

/** One problem the editor shows next to its field; `key` is under `policies.editor.errors`. */
export type ConfigProblem = { key: string; row?: number };

/** What the editor refuses before saving; an empty list lets the agent's validator decide. */
export function configProblems(checker: string, config: Config, checkpoints: readonly string[]): ConfigProblem[] {
  const problems: ConfigProblem[] = [];
  const unit = (value: number | undefined) => value === undefined || (value >= 0 && value <= 1);
  if (checker === "keyword_list" && readKeywords(config).length === 0) problems.push({ key: "keywordsEmpty" });
  if (checker === "regex") {
    const rows = readPatterns(config);
    if (rows.length === 0) problems.push({ key: "patternsEmpty" });
    rows.forEach((row, i) => {
      if (!REGEX_NAME.test(row.name)) problems.push({ key: "patternName", row: i });
      if (!patternCompiles(row.pattern)) problems.push({ key: "patternInvalid", row: i });
    });
  }
  if (checker === "presidio") {
    if (readEntities(config).length === 0) problems.push({ key: "entitiesEmpty" });
    if (!unit(readScoreThreshold(config))) problems.push({ key: "threshold" });
  }
  if (checker === "llm_judge") {
    if (!readTopic(config).trim()) problems.push({ key: "topicEmpty" });
    readExamples(config).forEach((example, i) => {
      if (!example.text.trim()) problems.push({ key: "exampleEmpty", row: i });
    });
    if (!unit(readMinConfidence(config))) problems.push({ key: "threshold" });
  }
  if (checker === "tier_ceiling" && !readMaxTier(config)) problems.push({ key: "tierEmpty" });
  const targets = readTargets(config);
  if (checkpoints.includes("DATA") && targets !== undefined && targets.length === 0) problems.push({ key: "targetsEmpty" });
  return problems;
}

/**
 * The config keys each editor reads and writes, with their readers: the editor writes a field
 * with `withField(config, key, value)` and nothing else, so a config the editor loads and saves
 * unchanged is the identical config. `targets` is edited for DATA policies in every editor.
 */
export const EDITOR_FIELDS: Record<string, { key: string; read: (config: Config) => unknown }[]> = {
  keyword_list: [{ key: "keywords", read: readKeywords }],
  regex: [{ key: "patterns", read: readPatterns }],
  presidio: [
    { key: "entities", read: readEntities },
    { key: "score_threshold", read: readScoreThreshold },
  ],
  llm_judge: [
    { key: "topic_description", read: readTopic },
    { key: "examples", read: readExamples },
    { key: "min_confidence", read: readMinConfidence },
  ],
  tier_ceiling: [
    { key: "max_tier", read: readMaxTier },
    { key: "roles", read: readRoles },
  ],
};
export const TARGETS_FIELD = { key: "targets", read: readTargets };
