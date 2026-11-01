"use client";

import { useTranslations } from "next-intl";
import { useState, type ReactNode } from "react";
import {
  DEFAULT_MIN_CONFIDENCE,
  DEFAULT_SCORE_THRESHOLD,
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
  type Config,
} from "@/lib/policyConfig";

const quiet = "rounded-md px-3 py-1.5 text-sm text-fg ring-1 ring-line-strong hover:bg-surface-2 disabled:opacity-50";
const chip = "rounded-full bg-surface-2 px-1.5 text-xs text-fg";
// Latin identifiers only: a monospace face never holds Arabic text (ADR-0119).
const code = `${chip} font-mono`;

/** A label of the locale's catalog when it has one, else the identifier as it is. */
export function useLabel() {
  const t = useTranslations();
  return (namespace: string, id: string): { text: string; known: boolean } => {
    const key = `${namespace}.${id}`;
    return t.has(key) ? { text: t(key), known: true } : { text: id, known: false };
  };
}

/** One item as a chip: an Arabic label in the sans face, an unknown identifier in monospace. */
export function LabelChip({ namespace, id }: { namespace: string; id: string }) {
  const label = useLabel()(namespace, id);
  return label.known ? (
    <bdi className={chip} title={id} data-item={id}>
      {label.text}
    </bdi>
  ) : (
    <bdi dir="ltr" className={code} data-item={id}>
      {id}
    </bdi>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-1 sm:grid-cols-[10rem_minmax(0,1fr)]" data-summary-row="">
      <dt className="text-xs font-medium text-muted">{label}</dt>
      <dd className="flex min-w-0 flex-wrap items-center gap-1 text-sm text-fg">{children}</dd>
    </div>
  );
}

function Value({ value, fallback }: { value: number | undefined; fallback: number }) {
  const t = useTranslations();
  return value === undefined ? (
    <span>{t("policies.config.default", { value: fallback })}</span>
  ) : (
    <bdi dir="ltr">{value}</bdi>
  );
}

/**
 * A policy's configuration as a reader sees it (ADR-0123): Arabic labels for entities, targets,
 * tiers and roles; keywords, patterns, the topic and its examples as written. The raw JSON sits
 * behind a toggle.
 */
export function ConfigSummary({ checker, config, checkpoints }: { checker: string; config: Config; checkpoints: string[] }) {
  const t = useTranslations();
  const [technical, setTechnical] = useState(false);
  const targets = readTargets(config);
  const roles = readRoles(config);

  return (
    <div className="mt-3" data-config-summary={checker}>
      <dl className="space-y-2">
        {checker === "keyword_list" && (
          <>
            <Row label={t("policies.config.keywords")}>
              {readKeywords(config).map((word) => (
                <bdi key={word} dir="auto" className={chip} data-item={word}>
                  {word}
                </bdi>
              ))}
            </Row>
            <Row label={t("policies.config.mode")}>
              {t(`policies.config.modes.${config.mode === "allow" ? "allow" : "deny"}`)}
            </Row>
          </>
        )}
        {checker === "regex" && (
          <>
            <Row label={t("policies.config.patterns")}>
              <ul className="w-full space-y-1">
                {readPatterns(config).map((row, i) => (
                  <li key={`${row.name}-${i}`} className="flex min-w-0 items-start gap-2" data-pattern={row.name}>
                    <bdi dir="ltr" className={code}>
                      {row.name}
                    </bdi>
                    <bdi dir="ltr" className="min-w-0 break-all font-mono text-xs text-fg">
                      {row.pattern}
                    </bdi>
                  </li>
                ))}
              </ul>
            </Row>
            <Row label={t("policies.config.fold")}>{config.fold === false ? t("policies.config.no") : t("policies.config.yes")}</Row>
          </>
        )}
        {checker === "presidio" && (
          <>
            <Row label={t("policies.config.entities")}>
              {readEntities(config).map((entity) => (
                <LabelChip key={entity} namespace="policies.entities" id={entity} />
              ))}
            </Row>
            <Row label={t("policies.config.scoreThreshold")}>
              <Value value={readScoreThreshold(config)} fallback={DEFAULT_SCORE_THRESHOLD} />
            </Row>
          </>
        )}
        {checker === "llm_judge" && (
          <>
            <Row label={t("policies.config.topic")}>
              <p dir="auto" className="whitespace-pre-wrap">
                {readTopic(config)}
              </p>
            </Row>
            <Row label={t("policies.config.examples")}>
              {readExamples(config).length === 0 ? (
                t("policies.config.none")
              ) : (
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-xs text-muted">
                      <th className="py-1 text-start font-medium">{t("policies.config.exampleText")}</th>
                      <th className="py-1 text-start font-medium">{t("policies.config.verdict")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {readExamples(config).map((example, i) => (
                      <tr key={i} className="border-t border-line" data-example-row="">
                        <td dir="auto" className="py-1 pe-2">
                          {example.text}
                        </td>
                        <td className="py-1">
                          {example.violates ? t("policies.config.violates") : t("policies.config.allowed")}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Row>
            <Row label={t("policies.config.minConfidence")}>
              <Value value={readMinConfidence(config)} fallback={DEFAULT_MIN_CONFIDENCE} />
            </Row>
          </>
        )}
        {checker === "tier_ceiling" && (
          <>
            <Row label={t("policies.config.tier")}>
              <LabelChip namespace="policies.tiers" id={readMaxTier(config)} />
            </Row>
            <Row label={t("policies.config.roles")}>
              {roles === undefined
                ? t("policies.config.allRoles")
                : roles.map((role) => <LabelChip key={role} namespace="policies.modelRoles" id={role} />)}
            </Row>
          </>
        )}
        {checkpoints.includes("DATA") && (
          <Row label={t("policies.config.targets")}>
            {targets === undefined
              ? t("policies.config.allTargets")
              : targets.map((target) => <LabelChip key={target} namespace="policies.targets" id={target} />)}
          </Row>
        )}
      </dl>
      <button type="button" className={`${quiet} mt-3`} aria-expanded={technical} onClick={() => setTechnical(!technical)}>
        {technical ? t("policies.summary.hideTechnical") : t("policies.summary.showTechnical")}
      </button>
      {technical && (
        <pre dir="ltr" className="mt-2 max-h-64 overflow-auto rounded-md bg-surface-2 p-2 text-xs text-fg" data-raw-config="">
          {JSON.stringify(config, null, 2)}
        </pre>
      )}
    </div>
  );
}
