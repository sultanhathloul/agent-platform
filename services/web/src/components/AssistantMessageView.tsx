"use client";

import { useLocale, useTranslations } from "next-intl";
import { resolveErrorMessage } from "@/lib/errors";
import { isLocale, DEFAULT_LOCALE } from "@/lib/locale";
import { safeHref } from "@/lib/return-path";
import type { ArtifactRef, Citation } from "@/lib/api/types";
import { shownChips, type ShownChip } from "@/lib/chips";
import type { AssistantMessage } from "@/lib/stream";
import { useIsAdmin } from "./MeProvider";
import { MessageMarkdown } from "./MessageMarkdown";
import { ReportCard } from "./ReportPanel";

const chipClass =
  "inline-flex items-center gap-1 rounded-full bg-surface-2 px-2 py-0.5 text-[11px] text-muted ring-1 ring-line";

function ChipView({ chip }: { chip: ShownChip }) {
  const t = useTranslations();
  const rawLocale = useLocale();
  const locale = isLocale(rawLocale) ? rawLocale : DEFAULT_LOCALE;
  switch (chip.kind) {
    case "tier":
      return (
        <li className={chipClass} title={chip.reason}>
          <span>{t("chips.tier")}</span>
          <bdi className="font-mono font-medium">{chip.tier}</bdi>
        </li>
      );
    case "guard": {
      const actionKey = `guardActions.${chip.action}`;
      const targetKey = `policies.targets.${chip.target}`;
      // A guard event names who decided what, never the checker's detail (ADR-0116).
      const title = [chip.checkpoint, chip.policyName].filter(Boolean).join(" · ");
      return (
        <li className={chipClass} title={title || undefined}>
          <span>{t("chips.guard")}</span>
          <bdi className="font-mono">
            {chip.policyId ? `${chip.policyId}@${chip.policyVersion ?? "?"}` : chip.checkpoint}
          </bdi>
          {t.has(actionKey) ? <span>{t(actionKey)}</span> : <bdi>{chip.action}</bdi>}
          {chip.target && t.has(targetKey) && <span className="text-subtle">{t(targetKey)}</span>}
        </li>
      );
    }
    case "tool": {
      const statusKey = `toolStatus.${chip.status}`;
      // A tool's label in the user's language (I13); a tool without one keeps the generic chip.
      return (
        <li className={chipClass} title={chip.label ? chip.tool : undefined}>
          {chip.label ? (
            <span>{locale === "ar" ? chip.label.ar : chip.label.en}</span>
          ) : (
            <>
              <span>{t("chips.tool")}</span>
              <bdi className="font-mono">{chip.tool}</bdi>
            </>
          )}
          {t.has(statusKey) ? <span>{t(statusKey)}</span> : <bdi>{chip.status}</bdi>}
          {chip.count > 1 && <span className="font-medium">{t("chips.count", { count: chip.count })}</span>}
          {chip.latencyMs !== null && <span>{t("chips.latency", { ms: chip.latencyMs })}</span>}
        </li>
      );
    }
  }
}

/**
 * The source line under an answer: the file name and the page or pages the answer drew on
 * (ADR-0140), or the section of a document without pages; a web source keeps its link; a knowledge
 * base document shows its KB, title, publisher and publication date too (phase 08, I4).
 */
export function CitationList({ citations }: { citations: Citation[] }) {
  const t = useTranslations();
  const rawLocale = useLocale();
  const locale = isLocale(rawLocale) ? rawLocale : DEFAULT_LOCALE;
  const tag = locale === "ar" ? "ar" : "en";
  const number = new Intl.NumberFormat(tag);
  const list = new Intl.ListFormat(tag, { type: "conjunction" });
  return (
    <section className="mt-3 border-t border-line pt-2">
      <h3 className="text-xs font-semibold text-muted">{t("chat.citationsTitle")}</h3>
      <ul className="mt-1 space-y-0.5 text-sm">
        {citations.map((c) => {
          const href = safeHref(c.url);
          const pages = c.pages ?? [];
          const kbName = c.kb_name ? (locale === "ar" ? c.kb_name.ar : c.kb_name.en) : null;
          return (
            <li key={c.id} className="flex flex-wrap items-baseline gap-x-2" data-citation-kb={c.kb ?? undefined}>
              {kbName && (
                // A knowledge base document: KB › title, then its publisher and date (phase 08, I4).
                <span className="text-xs font-medium text-primary">
                  {t.rich("chat.citationKb", {
                    kb: () => <bdi dir="auto">{kbName}</bdi>,
                    sep: (chunks) => (
                      <span aria-hidden className="px-1 text-subtle">
                        {chunks}
                      </span>
                    ),
                  })}
                </span>
              )}
              {href ? (
                <a href={href} target="_blank" rel="noopener noreferrer" className="text-info underline">
                  <bdi dir="auto">{c.title}</bdi>
                </a>
              ) : (
                <bdi dir="auto" className="font-medium text-fg">
                  {c.title}
                </bdi>
              )}
              {pages.length > 0 ? (
                <span className="text-xs text-muted">
                  {t(pages.length === 1 ? "chat.citationPageOne" : pages.length === 2 ? "chat.citationPageTwo" : "chat.citationPageMany", {
                    pages: list.format(pages.map((p) => number.format(p))),
                  })}
                </span>
              ) : (
                c.locator && (
                  <bdi dir="auto" className="text-xs text-muted">
                    {c.locator}
                  </bdi>
                )
              )}
              {c.publisher && (
                <bdi dir="auto" className="text-xs text-muted">
                  {c.publisher}
                </bdi>
              )}
              {c.published_on && (
                // ISO date in Western digits in both languages (ADR-0288).
                <span className="text-xs text-muted" dir="ltr">
                  {t("chat.citationDate", { date: c.published_on })}
                </span>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export function AssistantMessageView({
  message,
  onOpenReport,
}: {
  message: AssistantMessage;
  /** Reopens the report panel at the version this reply wrote (reports PR, I7). */
  onOpenReport?: (ref: ArtifactRef) => void;
}) {
  const t = useTranslations();
  const rawLocale = useLocale();
  const locale = isLocale(rawLocale) ? rawLocale : DEFAULT_LOCALE;
  const chips = shownChips(message.chips, { admin: useIsAdmin() });

  return (
    <article className="max-w-[90%] self-start rounded-2xl bg-surface px-4 py-3 shadow-sm ring-1 ring-line">
      <h2 className="mb-1 text-xs font-medium text-muted">{t("chat.assistant")}</h2>

      {(chips.length > 0 || message.skill) && (
        <ul className="mb-2 flex flex-wrap gap-1.5">
          {message.skill && (
            // The skill the answer was written with (phase 06): from the `skill` event or the history.
            <li className={chipClass} data-skill={message.skill.slug}>
              <span>{t("chips.skill")}</span>
              <span className="font-medium">{locale === "ar" ? message.skill.label.ar : message.skill.label.en}</span>
            </li>
          )}
          {chips.map((chip) => (
            <ChipView key={chip.key} chip={chip} />
          ))}
        </ul>
      )}

      {message.text ? (
        <MessageMarkdown text={message.text} />
      ) : (
        message.status === "streaming" && <p className="animate-pulse text-muted">{t("chat.thinking")}</p>
      )}

      {message.artifact && <ReportCard artifact={message.artifact} onOpen={onOpenReport} />}

      {message.status === "aborted" && <p className="mt-2 text-xs text-warning">{t("chat.stopped")}</p>}

      {message.status === "error" && message.error && (
        <p role="alert" className="mt-2 text-sm text-error">
          {resolveErrorMessage(message.error, locale, (key) => t(key))}
        </p>
      )}

      {message.citations.length > 0 && <CitationList citations={message.citations} />}

      {message.usage && (
        <p className="mt-2 text-[11px] text-subtle">
          {t("chat.usage", { tokensIn: message.usage.tokens_in ?? 0, tokensOut: message.usage.tokens_out ?? 0 })}
        </p>
      )}
    </article>
  );
}
