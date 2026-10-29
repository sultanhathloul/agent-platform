"use client";

import { useLocale, useTranslations } from "next-intl";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import type { ConversationSummary } from "@/lib/api/types";
import { listDate } from "@/lib/conversations";
import { listConversations } from "@/lib/gateway";
import { DEFAULT_LOCALE, isLocale } from "@/lib/locale";
import { useAuth } from "./AuthProvider";
import { useRuntimeConfig } from "./ConfigProvider";

/**
 * The caller's conversations, newest first (ADR-0192): each opens its history. Reloaded when
 * `refreshKey` changes, after a turn finishes. Read-only: no rename or delete (ADR-0166).
 */
export function ConversationSidebar({ currentId, refreshKey }: { currentId: string; refreshKey: number }) {
  const t = useTranslations("conversations");
  const rawLocale = useLocale();
  const locale = isLocale(rawLocale) ? rawLocale : DEFAULT_LOCALE;
  const { gatewayUrl } = useRuntimeConfig();
  const { getAccessToken } = useAuth();
  const [items, setItems] = useState<ConversationSummary[]>([]);
  const [next, setNext] = useState<string | undefined>();
  const [state, setState] = useState<"loading" | "ready" | "failed">("loading");

  const load = useCallback(
    async (before?: string) => {
      const token = await getAccessToken();
      if (!token) return;
      try {
        const page = await listConversations({ gatewayUrl, token, before });
        setItems((all) => (before ? [...all, ...page.conversations] : page.conversations));
        setNext(page.next_before ?? undefined);
        setState("ready");
      } catch {
        setState("failed");
      }
    },
    [gatewayUrl, getAccessToken],
  );

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  return (
    <nav aria-label={t("title")} className="hidden w-72 shrink-0 flex-col border-e border-line bg-surface md:flex">
      <h2 className="px-4 pb-2 pt-4 text-sm font-semibold text-muted">{t("title")}</h2>
      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-4">
        {state === "loading" && <p className="px-2 text-sm text-muted">{t("loading")}</p>}
        {state === "failed" && <p className="px-2 text-sm text-error">{t("loadFailed")}</p>}
        {state === "ready" && items.length === 0 && <p className="px-2 text-sm text-muted">{t("empty")}</p>}
        <ul className="flex flex-col gap-0.5">
          {items.map((item) => {
            const current = item.id === currentId;
            return (
              <li key={item.id}>
                <Link
                  href={`/chat/${item.id}`}
                  aria-current={current ? "page" : undefined}
                  className={`flex flex-col rounded-lg px-3 py-2 text-sm hover:bg-surface-2 ${current ? "bg-accent-subtle ring-1 ring-accent" : ""}`}
                >
                  <bdi dir="auto" className="truncate text-fg" title={item.title}>
                    {item.title || t("untitled")}
                  </bdi>
                  <span className="text-xs text-muted">{listDate(item.last_turn_at, locale)}</span>
                </Link>
              </li>
            );
          })}
        </ul>
        {next && (
          <button
            type="button"
            onClick={() => void load(next)}
            className="mt-2 w-full rounded-lg px-3 py-2 text-sm text-primary hover:bg-success-bg"
          >
            {t("loadMore")}
          </button>
        )}
      </div>
    </nav>
  );
}
