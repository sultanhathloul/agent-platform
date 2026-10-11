"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";
import {
  DATA_TARGETS,
  DEFAULT_MIN_CONFIDENCE,
  DEFAULT_SCORE_THRESHOLD,
  EDITOR_FIELDS,
  MODEL_ROLES,
  MODEL_TIERS,
  PRESIDIO_ENTITIES,
  addKeywords,
  configProblems,
  configToText,
  parseConfig,
  readEntities,
  readExamples,
  readKeywords,
  readMaxTier,
  readMinConfidence,
  readPatterns,
  readRoles,
  readScoreThreshold,
  readTargets,
  readTopic,
  toggleEntity,
  toggleIn,
  withField,
  type Config,
  type ConfigProblem,
} from "@/lib/policyConfig";
import { LabelChip } from "./ConfigSummary";

const input = "w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm";
const small = "rounded-md px-2 py-1 text-xs text-slate-700 ring-1 ring-slate-300 hover:bg-slate-50";
const label = "flex flex-col gap-1 text-sm text-slate-700";
// A symbol, the same in both locales; the button's name comes from the catalog.
const REMOVE_MARK = "×";

function Problems({ problems }: { problems: ConfigProblem[] }) {
  const t = useTranslations();
  if (problems.length === 0) return null;
  return (
    <>
      {problems.map((p) => (
        <span key={`${p.key}-${p.row ?? ""}`} role="alert" className="text-[11px] text-red-700" data-problem={p.key}>
          {t(`policies.editor.errors.${p.key}`)}
        </span>
      ))}
    </>
  );
}

function NumberField({
  text,
  value,
  fallback,
  onChange,
}: {
  text: string;
  value: number | undefined;
  fallback: number;
  onChange: (value: number | undefined) => void;
}) {
  return (
    <label className={label}>
      {text}
      <input
        type="number"
        dir="ltr"
        min={0}
        max={1}
        step={0.05}
        className={`${input} max-w-32`}
        value={value ?? ""}
        placeholder={String(fallback)}
        onChange={(e) => onChange(e.target.value === "" ? undefined : Number(e.target.value))}
      />
    </label>
  );
}

/**
 * The policy form's configuration (ADR-0123): one editor per checker, the DATA targets in every
 * editor, and "Advanced settings", a JSON view of the same text. The text is the one state both
 * views edit, so they cannot drift; while the text is not a JSON object the editor is paused and
 * saving is blocked. An editor writes only the fields it shows, so every other key is kept.
 */
export function ConfigEditor({
  checker,
  configText,
  checkpoints,
  onChange,
}: {
  checker: string;
  configText: string;
  checkpoints: string[];
  onChange: (configText: string) => void;
}) {
  const t = useTranslations();
  const [advanced, setAdvanced] = useState(false);
  const [keyword, setKeyword] = useState("");
  const parsed = parseConfig(configText);
  const config: Config = parsed.ok ? parsed.config : {};
  const known = checker in EDITOR_FIELDS;
  const problems = parsed.ok ? configProblems(checker, config, checkpoints) : [];
  const at = (key: string, row?: number) => problems.filter((p) => p.key === key && p.row === row);
  const set = (key: string, value: unknown) => {
    if (parsed.ok) onChange(configToText(withField(config, key, value)));
  };
  const showAdvanced = advanced || !parsed.ok || !known;

  const keywords = readKeywords(config);
  const patterns = readPatterns(config);
  const entities = readEntities(config);
  const examples = readExamples(config);
  const targets = readTargets(config);
  const roles = readRoles(config);
  const tier = readMaxTier(config);
  const addTyped = () => {
    if (!keyword.trim()) return;
    set("keywords", addKeywords(keywords, keyword));
    setKeyword("");
  };

  return (
    <fieldset className="flex flex-col gap-3 sm:col-span-2" data-config-editor={checker}>
      <legend className="text-sm text-slate-700">{t("policies.fields.config")}</legend>
      {!parsed.ok && (
        <p role="alert" className="text-xs text-red-700" data-problem="paused">
          {t("policies.editor.paused")}
        </p>
      )}
      {!known && <p className="text-xs text-slate-500">{t("policies.editor.noChecker")}</p>}
      <fieldset disabled={!parsed.ok} className="flex flex-col gap-3 disabled:opacity-60">
        {checker === "keyword_list" && (
          <div className={label}>
            {t("policies.config.keywords")}
            <ul className="flex flex-wrap gap-1" data-tags="">
              {keywords.map((word) => (
                <li key={word} className="flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-xs">
                  <bdi dir="auto">{word}</bdi>
                  <button
                    type="button"
                    className="text-slate-500 hover:text-red-700"
                    aria-label={t("policies.editor.removeItem", { item: word })}
                    onClick={() => set("keywords", keywords.filter((w) => w !== word))}
                  >
                    {REMOVE_MARK}
                  </button>
                </li>
              ))}
            </ul>
            <div className="flex gap-2">
              <input
                dir="auto"
                className={input}
                value={keyword}
                placeholder={t("policies.editor.keywordPlaceholder")}
                aria-label={t("policies.config.keywords")}
                onChange={(e) => setKeyword(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addTyped();
                  }
                }}
              />
              <button type="button" className={small} onClick={addTyped}>
                {t("policies.editor.add")}
              </button>
            </div>
            <Problems problems={at("keywordsEmpty")} />
          </div>
        )}

        {checker === "regex" && (
          <div className={label}>
            {t("policies.config.patterns")}
            <ul className="flex flex-col gap-2">
              {patterns.map((row, i) => (
                <li key={i} className="flex flex-col gap-1" data-pattern-row={i}>
                  <div className="flex gap-2">
                    <input
                      dir="ltr"
                      className={`${input} max-w-48 font-mono`}
                      value={row.name}
                      aria-label={t("policies.config.patternName")}
                      placeholder={t("policies.config.patternName")}
                      onChange={(e) => set("patterns", patterns.map((p, j) => (j === i ? { ...p, name: e.target.value } : p)))}
                    />
                    <input
                      dir="ltr"
                      className={`${input} font-mono`}
                      value={row.pattern}
                      aria-label={t("policies.config.pattern")}
                      placeholder={t("policies.config.pattern")}
                      onChange={(e) =>
                        set("patterns", patterns.map((p, j) => (j === i ? { ...p, pattern: e.target.value } : p)))
                      }
                    />
                    <button type="button" className={small} onClick={() => set("patterns", patterns.filter((_, j) => j !== i))}>
                      {t("policies.editor.remove")}
                    </button>
                  </div>
                  <Problems problems={[...at("patternName", i), ...at("patternInvalid", i)]} />
                </li>
              ))}
            </ul>
            <div>
              <button type="button" className={small} onClick={() => set("patterns", [...patterns, { name: "", pattern: "" }])}>
                {t("policies.editor.addPattern")}
              </button>
            </div>
            <Problems problems={at("patternsEmpty")} />
          </div>
        )}

        {checker === "presidio" && (
          <>
            <div className={label}>
              {t("policies.config.entities")}
              <div className="flex flex-wrap gap-3">
                {PRESIDIO_ENTITIES.map((entity) => (
                  <label key={entity} className="flex items-center gap-1 text-sm" data-entity={entity}>
                    <input
                      type="checkbox"
                      checked={entities.includes(entity)}
                      onChange={(e) => set("entities", toggleEntity(entities, entity, e.target.checked))}
                    />
                    {t(`policies.entities.${entity}`)}
                  </label>
                ))}
                {entities
                  .filter((entity) => !(PRESIDIO_ENTITIES as readonly string[]).includes(entity))
                  .map((entity) => (
                    <LabelChip key={entity} namespace="policies.entities" id={entity} />
                  ))}
              </div>
              <Problems problems={at("entitiesEmpty")} />
            </div>
            <NumberField
              text={t("policies.config.scoreThreshold")}
              value={readScoreThreshold(config)}
              fallback={DEFAULT_SCORE_THRESHOLD}
              onChange={(value) => set("score_threshold", value)}
            />
            <Problems problems={at("threshold")} />
          </>
        )}

        {checker === "llm_judge" && (
          <>
            <label className={label}>
              {t("policies.config.topic")}
              <textarea
                dir="auto"
                rows={3}
                className={input}
                value={readTopic(config)}
                onChange={(e) => set("topic_description", e.target.value)}
              />
              <Problems problems={at("topicEmpty")} />
            </label>
            <div className={label}>
              {t("policies.config.examples")}
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-xs text-slate-500">
                    <th className="py-1 text-start font-medium">{t("policies.config.exampleText")}</th>
                    <th className="py-1 text-start font-medium">{t("policies.config.verdict")}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {examples.map((example, i) => (
                    <tr key={i} className="align-top" data-example-row={i}>
                      <td className="py-1 pe-2">
                        <input
                          dir="auto"
                          className={input}
                          value={example.text}
                          aria-label={t("policies.config.exampleText")}
                          onChange={(e) =>
                            set("examples", examples.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)))
                          }
                        />
                        <Problems problems={at("exampleEmpty", i)} />
                      </td>
                      <td className="py-1 pe-2">
                        <select
                          className={input}
                          aria-label={t("policies.config.verdict")}
                          value={example.violates ? "violates" : "allowed"}
                          onChange={(e) =>
                            set(
                              "examples",
                              examples.map((x, j) => (j === i ? { ...x, violates: e.target.value === "violates" } : x)),
                            )
                          }
                        >
                          <option value="violates">{t("policies.config.violates")}</option>
                          <option value="allowed">{t("policies.config.allowed")}</option>
                        </select>
                      </td>
                      <td className="py-1">
                        <button type="button" className={small} onClick={() => set("examples", examples.filter((_, j) => j !== i))}>
                          {t("policies.editor.remove")}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div>
                <button type="button" className={small} onClick={() => set("examples", [...examples, { text: "", violates: true }])}>
                  {t("policies.editor.addExample")}
                </button>
              </div>
            </div>
            <NumberField
              text={t("policies.config.minConfidence")}
              value={readMinConfidence(config)}
              fallback={DEFAULT_MIN_CONFIDENCE}
              onChange={(value) => set("min_confidence", value)}
            />
            <Problems problems={at("threshold")} />
          </>
        )}

        {checker === "tier_ceiling" && (
          <>
            <label className={label}>
              {t("policies.config.tier")}
              <select className={`${input} max-w-48`} value={tier} onChange={(e) => set("max_tier", e.target.value)}>
                {!tier && <option value="" />}
                {[...MODEL_TIERS, ...(tier && !(MODEL_TIERS as readonly string[]).includes(tier) ? [tier] : [])].map((x) => (
                  <option key={x} value={x}>
                    {t.has(`policies.tiers.${x}`) ? t(`policies.tiers.${x}`) : x}
                  </option>
                ))}
              </select>
              <Problems problems={at("tierEmpty")} />
            </label>
            <div className={label}>
              {t("policies.config.roles")}
              <div className="flex flex-wrap gap-3">
                {MODEL_ROLES.map((role) => (
                  <label key={role} className="flex items-center gap-1 text-sm" data-model-role={role}>
                    <input
                      type="checkbox"
                      checked={roles === undefined || roles.includes(role)}
                      onChange={(e) => set("roles", toggleIn(roles, MODEL_ROLES, role, e.target.checked))}
                    />
                    {t(`policies.modelRoles.${role}`)}
                  </label>
                ))}
              </div>
            </div>
          </>
        )}

        {checkpoints.includes("DATA") && (
          <div className={label} data-targets="">
            {t("policies.config.targets")}
            <div className="flex flex-wrap gap-3">
              {DATA_TARGETS.map((target) => (
                <label key={target} className="flex items-center gap-1 text-sm" data-target={target}>
                  <input
                    type="checkbox"
                    checked={targets === undefined || targets.includes(target)}
                    onChange={(e) => set("targets", toggleIn(targets, DATA_TARGETS, target, e.target.checked))}
                  />
                  {t(`policies.targets.${target}`)}
                </label>
              ))}
            </div>
            <Problems problems={at("targetsEmpty")} />
          </div>
        )}
      </fieldset>

      <div>
        <button
          type="button"
          className={small}
          aria-pressed={showAdvanced}
          disabled={!parsed.ok || !known}
          onClick={() => setAdvanced(!advanced)}
        >
          {t("policies.editor.advanced")}
        </button>
      </div>
      {showAdvanced && (
        <label className={label}>
          {t("policies.editor.advanced")}
          <textarea
            dir="ltr"
            rows={8}
            spellCheck={false}
            className={`${input} font-mono text-xs`}
            value={configText}
            onChange={(e) => onChange(e.target.value)}
            data-advanced-json=""
          />
          {!parsed.ok && (
            <span role="alert" className="text-[11px] text-red-700" data-problem="jsonInvalid">
              {t("policies.editor.jsonInvalid")}
            </span>
          )}
        </label>
      )}
    </fieldset>
  );
}
