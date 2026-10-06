"use client";

import { useLocale, useTranslations } from "next-intl";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import type { MemoryItem, MemoryList } from "@/lib/api/types";
import { resolveErrorMessage, type ErrorInfo } from "@/lib/errors";
import { GatewayError, deleteMemoryItem, getMemory, setMemoryEnabled } from "@/lib/gateway";
import { DEFAULT_LOCALE, isLocale } from "@/lib/locale";
import { useAuth } from "./AuthProvider";
import { useRuntimeConfig } from "./ConfigProvider";

type Status = "loading" | "ready" | "failed";

/** The caller's long-term memory (README §8): the switch, the saved items, and deleting one. */
export function MemorySettings() {
  const t = useTranslations();
  const rawLocale = useLocale();
  const locale = isLocale(rawLocale) ? rawLocale : DEFAULT_LOCALE;
  const { gatewayUrl } = useRuntimeConfig();
  const auth = useAuth();
  const pathname = usePathname();

  const [status, setStatus] = useState<Status>("loading");
  const [memory, setMemory] = useState<MemoryList | null>(null);
  const [error, setError] = useState<ErrorInfo | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // "switch" or the id of the item being deleted
  const [notice, setNotice] = useState<string | null>(null);

  const { getAccessToken, login } = auth;

  /** Runs `call` with a fresh token; a failure is shown, and an expired session signs in again. */
  const authorized = useCallback(
    async <T,>(call: (token: string) => Promise<T>): Promise<{ ok: true; value: T } | { ok: false }> => {
      const token = await getAccessToken();
      if (!token) {
        await login(pathname);
        return { ok: false };
      }
      try {
        return { ok: true, value: await call(token) };
      } catch (failure) {
        setError(failure instanceof GatewayError ? failure.info : { code: "unknown" });
        if (failure instanceof GatewayError && failure.status === 401) await login(pathname);
        return { ok: false };
      }
    },
    [getAccessToken, login, pathname],
  );

  const load = useCallback(async () => {
    setStatus("loading");
    setError(null);
    const result = await authorized((token) => getMemory({ gatewayUrl, token }));
    if (result.ok) setMemory(result.value);
    setStatus(result.ok ? "ready" : "failed");
  }, [authorized, gatewayUrl]);

  useEffect(() => {
    void load();
  }, [load]);

  async function toggle() {
    if (!memory) return;
    setBusy("switch");
    setError(null);
    setNotice(null);
    const result = await authorized((token) => setMemoryEnabled({ gatewayUrl, token, enabled: !memory.enabled }));
    if (result.ok) setMemory({ ...memory, enabled: result.value.enabled });
    setBusy(null);
  }

  async function remove(item: MemoryItem) {
    setBusy(item.id);
    setError(null);
    setNotice(null);
    const result = await authorized((token) => deleteMemoryItem({ gatewayUrl, token, id: item.id }));
    if (result.ok) {
      setMemory((current) => current && { ...current, items: current.items.filter((i) => i.id !== item.id) });
      setNotice(t("memory.deleted"));
    }
    setBusy(null);
  }

  const dateFormat = new Intl.DateTimeFormat(locale, { dateStyle: "medium" });

  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-8">
      <h1 className="text-2xl font-semibold text-slate-800">{t("memory.title")}</h1>
      <p className="mt-2 leading-7 text-slate-600">{t("memory.description")}</p>

      {error && (
        <p role="alert" className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 ring-1 ring-red-200">
          {resolveErrorMessage(error, locale, (key) => t(key))}
        </p>
      )}
      {notice && (
        <p role="status" className="mt-4 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
          {notice}
        </p>
      )}

      {status === "loading" && <p className="mt-8 text-sm text-slate-500">{t("memory.loading")}</p>}

      {status === "failed" && !memory && (
        <button
          type="button"
          onClick={() => void load()}
          className="mt-6 rounded-lg bg-emerald-700 px-4 py-2 font-medium text-white hover:bg-emerald-800"
        >
          {t("memory.retry")}
        </button>
      )}

      {memory && (
        <>
          <section className="mt-6 flex items-start justify-between gap-4 rounded-2xl bg-white p-4 shadow-sm ring-1 ring-slate-200">
            <p id="memory-switch-hint" className="text-sm leading-6 text-slate-600">
              {memory.enabled ? t("memory.enabledHint") : t("memory.disabledHint")}
            </p>
            <button
              type="button"
              role="switch"
              aria-checked={memory.enabled}
              aria-label={t("memory.toggleLabel")}
              aria-describedby="memory-switch-hint"
              disabled={busy !== null}
              onClick={() => void toggle()}
              className={`relative h-7 w-12 shrink-0 rounded-full transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-700 disabled:opacity-50 ${
                memory.enabled ? "bg-emerald-600" : "bg-slate-300"
              }`}
            >
              <span
                aria-hidden="true"
                className={`absolute top-1 size-5 rounded-full bg-white shadow transition-all ${
                  memory.enabled ? "start-6" : "start-1"
                }`}
              />
            </button>
          </section>

          {memory.items.length === 0 ? (
            <p className="mt-8 text-center text-slate-500">{t("memory.empty")}</p>
          ) : (
            <ul className="mt-6 flex flex-col gap-3">
              {memory.items.map((item) => {
                const kindKey = `memory.kinds.${item.kind}`;
                return (
                  <li
                    key={item.id}
                    className="flex items-start justify-between gap-4 rounded-2xl bg-white p-4 shadow-sm ring-1 ring-slate-200"
                  >
                    <div className="min-w-0">
                      <span className="inline-block rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600">
                        {t.has(kindKey) ? t(kindKey) : <bdi>{item.kind}</bdi>}
                      </span>
                      <p dir="auto" className="mt-1.5 leading-7 text-slate-800">
                        {item.text}
                      </p>
                      <p className="mt-1 text-xs text-slate-400">
                        {t("memory.savedOn", { date: dateFormat.format(new Date(item.created_at)) })}
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={() => void remove(item)}
                      disabled={busy !== null}
                      aria-label={t("memory.deleteLabel", { text: item.text })}
                      className="shrink-0 rounded-md px-2.5 py-1.5 text-sm text-red-700 ring-1 ring-red-200 hover:bg-red-50 disabled:opacity-50"
                    >
                      {busy === item.id ? t("memory.saving") : t("memory.delete")}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
