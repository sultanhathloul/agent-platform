"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { useLabel } from "./ConfigSummary";

/** The heading over the resulting text, named after the action (ADR-0123). */
export function OutputHeading({ action }: { action: string }) {
  const t = useTranslations();
  const key = `policies.test.outputBy.${action}`;
  return <h3 className="text-xs font-semibold text-muted">{t.has(key) ? t(key) : t("policies.test.output")}</h3>;
}

/**
 * An entity type as the presidio editor names it (رقم هوية أو إقامة، …), in the sans face; an
 * entity the catalog does not know (a regex pattern's name) as its identifier, in monospace.
 */
export function EntityName({ entity, className = "" }: { entity: string; className?: string }) {
  const label = useLabel()("policies.entities", entity);
  return label.known ? (
    <bdi className={className} title={entity} data-entity={entity}>
      {label.text}
    </bdi>
  ) : (
    <bdi dir="ltr" className={`${className} font-mono`} data-entity={entity}>
      {entity}
    </bdi>
  );
}

/**
 * A checker's explanation (ADR-0123), built from the catalog's sentence with each technical value
 * in its own <bdi>: `<v>` wraps one value, `<list>` stands for the matched items, entities by
 * their Arabic label. Without a key the checker's own English text is shown.
 */
export function Explanation({
  explanationKey,
  args,
  fallback,
}: {
  explanationKey?: string | null;
  args?: Record<string, string> | null;
  fallback?: string | null;
}) {
  const t = useTranslations();
  const key = `policies.explanation.${explanationKey ?? ""}`;
  if (!explanationKey || !t.has(key)) return <bdi dir="auto">{fallback}</bdi>;
  const values = { ...(args ?? {}) };
  const items = (values.items ?? "").split("\n").filter(Boolean);
  delete values.items;
  const entities = explanationKey === "entities";
  return (
    <span data-explanation={explanationKey}>
      {t.rich(key, {
        ...values,
        v: (chunks: ReactNode) => (
          // A value may be Arabic (the judge's category): its own direction, the sans face.
          <bdi dir="auto" data-value="">
            {chunks}
          </bdi>
        ),
        list: () => (
          <>
            {items.map((item, i) => (
              <span key={item}>
                {i > 0 && " "}
                {entities ? (
                  <EntityName entity={item} className="rounded bg-surface-2 px-1" />
                ) : (
                  <bdi dir="auto" className="rounded bg-surface-2 px-1" data-value="">
                    {item}
                  </bdi>
                )}
              </span>
            ))}
          </>
        ),
      })}
    </span>
  );
}
