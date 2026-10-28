"use client";

import { useLocale, useTranslations } from "next-intl";
import Link from "next/link";
import { type FormEvent, useCallback, useEffect, useState } from "react";
import type { KbView } from "@/lib/api/types";
import { resolveErrorMessage, type ErrorInfo } from "@/lib/errors";
import { GatewayError, createKb, deleteKb, listKbs } from "@/lib/gateway";
import { SLUG_PATTERN, labelIn, roleOf } from "@/lib/kbs";
import { DEFAULT_LOCALE, isLocale } from "@/lib/locale";
import { pageFormat } from "@/lib/settings";
import { useAuth } from "../AuthProvider";
import { useRuntimeConfig } from "../ConfigProvider";
import { Chip, button, card, quiet } from "./SettingsParts";

const field = "w-full rounded-md border border-slate-300 px-2.5 py-1.5 text-sm focus:border-emerald-600 focus:outline-none";

type Draft = { slug: string; nameAr: string; nameEn: string; descriptionAr: string; descriptionEn: string };
const EMPTY: Draft = { slug: "", nameAr: "", nameEn: "", descriptionAr: "", descriptionEn: "" };

/**
 * /admin/kbs (phase 08, I5): the tenant's knowledge bases with their role, document counts and who
 * created them; create one (its role kb:<slug>:read is created with it) and delete one (hidden, its
 * role kept, its documents purged from the index), confirmed on the page, never in a browser dialog.
 * Arabic first, Western digits (ADR-0288), no code or JSON to type.
 */
export function KbAdmin() {
  const t = useTranslations("kbs");
  const tr = useTranslations();
  const rawLocale = useLocale();
  const locale = isLocale(rawLocale) ? rawLocale : DEFAULT_LOCALE;
  const format = pageFormat(locale);
  const auth = useAuth();
  const config = useRuntimeConfig();
  const [kbs, setKbs] = useState<KbView[] | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [error, setError] = useState<ErrorInfo | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState<string | null>(null);

  const message = (info: ErrorInfo) => resolveErrorMessage(info, locale, (key) => tr(key));
  const failure = (e: unknown): ErrorInfo => (e instanceof GatewayError ? e.info : { code: "unknown" });

  const load = useCallback(async () => {
    const token = await auth.getAccessToken();
    if (!token) return;
    try {
      setKbs((await listKbs({ gatewayUrl: config.gatewayUrl, token })).kbs);
    } catch (e) {
      setError(failure(e));
    }
  }, [auth, config.gatewayUrl]);

  useEffect(() => {
    void load();
  }, [load]);

  const slugOk = SLUG_PATTERN.test(draft.slug);
  const ready = slugOk && draft.nameAr.trim() && draft.nameEn.trim();

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    if (!ready || busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const token = await auth.getAccessToken();
      if (!token) return;
      const created = await createKb({
        gatewayUrl: config.gatewayUrl,
        token,
        kb: {
          slug: draft.slug,
          name: { ar: draft.nameAr.trim(), en: draft.nameEn.trim() },
          description: { ar: draft.descriptionAr.trim(), en: draft.descriptionEn.trim() },
        },
      });
      setDraft(EMPTY);
      setNotice(t("created", { name: labelIn(created.name, locale) }));
      await load();
    } catch (err) {
      setError(failure(err));
    } finally {
      setBusy(false);
    }
  }

  async function onDelete(kb: KbView) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const token = await auth.getAccessToken();
      if (!token) return;
      const deleted = await deleteKb({ gatewayUrl: config.gatewayUrl, token, slug: kb.slug });
      setNotice(
        t(deleted.index_purged ? "deleted" : "deletedNotPurged", { name: labelIn(kb.name, locale) }),
      );
      setConfirming(null);
      await load();
    } catch (err) {
      setError(failure(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-4">
      <header>
        <h1 className="text-xl font-semibold">{t("title")}</h1>
        <p className="mt-1 text-sm text-slate-600">{t("intro")}</p>
      </header>

      {error && (
        <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-800">
          {message(error)}
        </p>
      )}
      {notice && (
        <p role="status" className="rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-900">
          {notice}
        </p>
      )}

      <form onSubmit={onCreate} className={card} aria-labelledby="kb-create">
        <h2 id="kb-create" className="mb-3 text-base font-semibold">
          {t("createTitle")}
        </h2>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block text-sm">
            <span className="text-slate-700">{t("nameAr")}</span>
            <input dir="auto" className={field} value={draft.nameAr} maxLength={120}
              onChange={(e) => setDraft({ ...draft, nameAr: e.target.value })} />
          </label>
          <label className="block text-sm">
            <span className="text-slate-700">{t("nameEn")}</span>
            <input dir="ltr" className={field} value={draft.nameEn} maxLength={120}
              onChange={(e) => setDraft({ ...draft, nameEn: e.target.value })} />
          </label>
          <label className="block text-sm">
            <span className="text-slate-700">{t("slug")}</span>
            <input dir="ltr" className={field} value={draft.slug} maxLength={63} aria-invalid={draft.slug !== "" && !slugOk}
              onChange={(e) => setDraft({ ...draft, slug: e.target.value.trim().toLowerCase() })} />
            <span className={`mt-0.5 block text-xs ${draft.slug && !slugOk ? "text-red-700" : "text-slate-500"}`}>
              {t("slugHint")}
            </span>
          </label>
          <div className="text-sm">
            <span className="text-slate-700">{t("role")}</span>
            <p className="mt-1.5">{slugOk ? <Chip name="role">{roleOf(draft.slug)}</Chip> : <span className="text-xs text-slate-400">{t("roleAfterSlug")}</span>}</p>
          </div>
          <label className="block text-sm">
            <span className="text-slate-700">{t("descriptionAr")}</span>
            <textarea dir="auto" rows={2} className={field} value={draft.descriptionAr} maxLength={500}
              onChange={(e) => setDraft({ ...draft, descriptionAr: e.target.value })} />
          </label>
          <label className="block text-sm">
            <span className="text-slate-700">{t("descriptionEn")}</span>
            <textarea dir="ltr" rows={2} className={field} value={draft.descriptionEn} maxLength={500}
              onChange={(e) => setDraft({ ...draft, descriptionEn: e.target.value })} />
          </label>
        </div>
        <div className="mt-3 flex items-center gap-3">
          <button type="submit" className={button} disabled={!ready || busy}>
            {t("create")}
          </button>
          <span className="text-xs text-slate-500">{t("createHint")}</span>
        </div>
      </form>

      <section className={card} aria-labelledby="kb-list">
        <h2 id="kb-list" className="mb-3 text-base font-semibold">
          {t("listTitle")}
        </h2>
        {kbs === null ? (
          <p className="text-sm text-slate-500">{t("loading")}</p>
        ) : kbs.length === 0 ? (
          <p className="text-sm text-slate-500">{t("empty")}</p>
        ) : (
          <ul className="divide-y divide-slate-100">
            {kbs.map((kb) => (
              <li key={kb.slug} className="flex flex-wrap items-center gap-x-4 gap-y-2 py-3" data-kb={kb.slug}>
                <div className="min-w-56 flex-1">
                  <Link href={`/admin/kbs/${encodeURIComponent(kb.slug)}`} className="font-medium text-emerald-800 hover:underline">
                    <bdi dir="auto">{labelIn(kb.name, locale)}</bdi>
                  </Link>
                  <p className="mt-0.5 text-xs text-slate-500">
                    {t("counts", { documents: format.number(kb.documents), indexed: format.number(kb.indexed) })}
                    {" · "}
                    {t("createdBy", {
                      time: format.time(kb.created_at),
                      name: kb.created_by_name ?? kb.created_by_username ?? "",
                    })}
                  </p>
                </div>
                <div className="flex items-center gap-1.5 text-xs text-slate-600">
                  <span>{t("role")}</span>
                  <Chip name="role">{kb.role}</Chip>
                </div>
                <div className="flex items-center gap-2">
                  <Link href={`/admin/kbs/${encodeURIComponent(kb.slug)}`} className={quiet}>
                    {t("open")}
                  </Link>
                  {confirming === kb.slug ? (
                    <>
                      <span className="text-xs text-red-800">{t("confirmDelete")}</span>
                      <button type="button" className={`${button} bg-red-700 hover:bg-red-800`} disabled={busy}
                        onClick={() => void onDelete(kb)}>
                        {t("delete")}
                      </button>
                      <button type="button" className={quiet} onClick={() => setConfirming(null)}>
                        {t("cancel")}
                      </button>
                    </>
                  ) : (
                    <button type="button" className={quiet} onClick={() => setConfirming(kb.slug)}>
                      {t("delete")}
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
