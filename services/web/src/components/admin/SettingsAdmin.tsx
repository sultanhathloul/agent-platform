"use client";

import { useLocale, useTranslations } from "next-intl";
import { useCallback, useEffect, useMemo, useState } from "react";
import type {
  PlatformSettingsView,
  PlatformUsage,
  SettingField,
  SettingsPreview,
  SettingsVersionView,
} from "@/lib/api/types";
import { resolveErrorMessage, type ErrorInfo } from "@/lib/errors";
import {
  GatewayError,
  getSettings,
  getUsage,
  listSettingsVersions,
  previewSettings,
  revertSettings,
  saveSettings,
} from "@/lib/gateway";
import { DEFAULT_LOCALE, isLocale } from "@/lib/locale";
import {
  GROUPS,
  USAGE_REFRESH_MS,
  byGroup,
  changedKeys,
  checkNumber,
  draftValue,
  overridesOf,
  restored,
  validTenantId,
  westernDigits,
  withValue,
  type FieldProblem,
  type Overrides,
} from "@/lib/settings";
import { useAuth } from "../AuthProvider";
import { useRuntimeConfig } from "../ConfigProvider";
import {
  ChangeList,
  ProblemList,
  ReadOnlyList,
  SectionHeading,
  SettingFieldRow,
  TimeoutTree,
  UsagePanel,
  VersionHistory,
  button,
  card,
  quiet,
  usePageFormat,
} from "./SettingsParts";

type Texts = Record<string, string>;
type TenantRows = Record<string, Array<[string, string]>>;

function textsOf(fields: SettingField[], overrides: Overrides): Texts {
  const out: Texts = {};
  for (const field of fields) {
    const value = draftValue(field, overrides);
    out[field.key] = typeof value === "number" ? String(value) : "";
  }
  return out;
}

function rowsOf(fields: SettingField[], overrides: Overrides): TenantRows {
  const out: TenantRows = {};
  for (const field of fields.filter((f) => f.per_tenant)) {
    const value = draftValue(field, overrides);
    out[field.key] = value && typeof value === "object" ? Object.entries(value).map(([k, v]) => [k, String(v)]) : [];
  }
  return out;
}

/** The platform settings page (owner decision 2026-10-22): admin only; the gateway and the agent check the role. */
export function SettingsAdmin() {
  const t = useTranslations();
  const locale = useLocale();
  const f = usePageFormat();
  const { getAccessToken } = useAuth();
  const { gatewayUrl } = useRuntimeConfig();
  const [view, setView] = useState<PlatformSettingsView | null>(null);
  const [versions, setVersions] = useState<SettingsVersionView[]>([]);
  const [overrides, setOverrides] = useState<Overrides>({});
  const [texts, setTexts] = useState<Texts>({});
  const [rows, setRows] = useState<TenantRows>({});
  const [problems, setProblems] = useState<Record<string, FieldProblem>>({});
  const [preview, setPreview] = useState<{ draft: string; result: SettingsPreview } | null>(null);
  const [usage, setUsage] = useState<PlatformUsage | null>(null);
  const [usageFailed, setUsageFailed] = useState(false);
  const [usageAt, setUsageAt] = useState<string | undefined>();
  const [confirming, setConfirming] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<ErrorInfo | null>(null);

  const message = useCallback(
    (info: ErrorInfo) =>
      resolveErrorMessage(info, isLocale(locale) ? locale : DEFAULT_LOCALE, (key) => t(key)),
    [locale, t],
  );

  const authorized = useCallback(async () => {
    const token = await getAccessToken();
    if (!token) throw new GatewayError(401, { code: "unauthorized" });
    return { gatewayUrl, token };
  }, [getAccessToken, gatewayUrl]);

  const reset = useCallback((next: PlatformSettingsView) => {
    const inForce = overridesOf(next.fields);
    setView(next);
    setOverrides(inForce);
    setTexts(textsOf(next.fields, inForce));
    setRows(rowsOf(next.fields, inForce));
    setProblems({});
    setPreview(null);
  }, []);

  const load = useCallback(async () => {
    try {
      const auth = await authorized();
      const [settings, history] = await Promise.all([getSettings(auth), listSettingsVersions(auth)]);
      reset(settings);
      setVersions(history.versions);
      setError(null);
    } catch (e) {
      setError(e instanceof GatewayError ? e.info : { code: "unknown" });
    }
  }, [authorized, reset]);

  useEffect(() => {
    void load();
  }, [load]);

  // "الاستهلاك الآن": every 5 s while the page is open and visible; a failed read keeps the last counts.
  // One random id per page load: the gateway audits its first read, then one per 10 minutes (ADR-0287).
  const [pageSession] = useState(() => crypto.randomUUID());
  useEffect(() => {
    let active = true;
    const read = async () => {
      if (typeof document !== "undefined" && document.hidden) return;
      try {
        const next = await getUsage({ ...(await authorized()), pageSession });
        if (!active) return;
        setUsage(next);
        setUsageFailed(false);
        setUsageAt(new Date().toISOString());
      } catch {
        if (active) setUsageFailed(true);
      }
    };
    void read();
    const timer = setInterval(() => void read(), USAGE_REFRESH_MS);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [authorized, pageSession]);

  const fields = useMemo(() => view?.fields ?? [], [view]);
  const inForce = useMemo(() => overridesOf(fields), [fields]);
  const changed = useMemo(() => changedKeys(fields, overrides), [fields, overrides]);
  const draftKey = JSON.stringify(overrides);
  const hasProblems = Object.values(problems).some((p) => p !== null);

  const setText = (field: SettingField, typed: string) => {
    const text = westernDigits(typed);
    setTexts((all) => ({ ...all, [field.key]: text }));
    const checked = checkNumber(text, field);
    setProblems((all) => ({ ...all, [field.key]: checked.problem }));
    if (checked.problem === null && checked.value !== null) {
      setOverrides((all) => withValue(all, field, checked.value));
    }
  };

  const setNoLimit = (field: SettingField, on: boolean) => {
    setProblems((all) => ({ ...all, [field.key]: null }));
    if (on) setOverrides((all) => withValue(all, field, null));
    else {
      const fallback = Math.min(field.maximum, Math.max(field.minimum, 1));
      setTexts((all) => ({ ...all, [field.key]: String(fallback) }));
      setOverrides((all) => withValue(all, field, fallback));
    }
  };

  const setTenants = (field: SettingField, typed: Array<[string, string]>) => {
    const next = typed.map(([tenant, count]): [string, string] => [tenant, westernDigits(count)]);
    setRows((all) => ({ ...all, [field.key]: next }));
    const map: Record<string, number> = {};
    let problem: FieldProblem = null;
    for (const [tenant, count] of next) {
      if (!tenant && !count) continue;
      if (!validTenantId(tenant)) problem = "tenant";
      const checked = checkNumber(count, field);
      if (checked.problem) problem = problem ?? checked.problem;
      else if (checked.value !== null) map[tenant] = checked.value;
    }
    setProblems((all) => ({ ...all, [field.key]: problem }));
    if (problem === null) setOverrides((all) => withValue(all, field, map));
  };

  const restore = (field: SettingField) => {
    const next = restored(overrides, field.key);
    setOverrides(next);
    setTexts((all) => ({ ...all, ...textsOf([field], next) }));
    setRows((all) => ({ ...all, ...rowsOf([field], next) }));
    setProblems((all) => ({ ...all, [field.key]: null }));
  };

  const review = async () => {
    setBusy(true);
    setNotice(null);
    try {
      const result = await previewSettings({ ...(await authorized()), draft: { overrides } });
      setPreview({ draft: draftKey, result });
      setError(null);
    } catch (e) {
      setError(e instanceof GatewayError ? e.info : { code: "unknown" });
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!view) return;
    setBusy(true);
    try {
      const saved = await saveSettings({
        ...(await authorized()),
        draft: { overrides, expected_version: view.version },
      });
      reset(saved.settings);
      setVersions((await listSettingsVersions(await authorized())).versions);
      setNotice(t("settings.review.saved", { version: f.number(saved.version) }));
      setError(null);
    } catch (e) {
      setError(e instanceof GatewayError ? e.info : { code: "unknown" });
    } finally {
      setBusy(false);
    }
  };

  const revert = async () => {
    if (!view || confirming === null) return;
    setBusy(true);
    try {
      const saved = await revertSettings({ ...(await authorized()), version: confirming, expectedVersion: view.version });
      reset(saved.settings);
      setVersions((await listSettingsVersions(await authorized())).versions);
      setNotice(t("settings.history.reverted", { from: f.number(confirming), version: f.number(saved.version) }));
      setConfirming(null);
      setError(null);
    } catch (e) {
      setError(e instanceof GatewayError ? e.info : { code: "unknown" });
    } finally {
      setBusy(false);
    }
  };

  if (!view) {
    return (
      <main className="mx-auto max-w-4xl p-6">
        {error ? (
          <p role="alert" className="text-sm text-error">
            {message(error)}
          </p>
        ) : (
          <p className="text-sm text-muted">{t("settings.loading")}</p>
        )}
      </main>
    );
  }

  const reviewed = preview !== null && preview.draft === draftKey;
  return (
    <main className="mx-auto max-w-4xl space-y-4 p-6">
      <header>
        <h1 className="text-xl font-semibold">{t("settings.title")}</h1>
        <p className="mt-1 text-sm text-muted">{t("settings.description")}</p>
        <p className="mt-1 text-xs text-muted">
          {view.version > 0 ? t("settings.versionInForce", { version: f.number(view.version) }) : t("settings.versionFiles")}
        </p>
        {view.ignored.length > 0 && (
          <p role="alert" className="mt-2 rounded-md bg-warning-bg p-2 text-sm text-warning">
            {t("settings.ignored")}
          </p>
        )}
      </header>
      {error && (
        <p role="alert" className="rounded-md bg-error-bg p-2 text-sm text-error">
          {message(error)}
        </p>
      )}
      {notice && (
        <p role="status" className="rounded-md bg-success-bg p-2 text-sm text-success">
          {notice}
        </p>
      )}
      {GROUPS.map((group) => (
        <section key={group} className={card} data-group={group}>
          <SectionHeading group={group} />
          {byGroup(fields, group).map((field) => (
            <SettingFieldRow
              key={field.key}
              field={field}
              overrides={overrides}
              text={texts[field.key] ?? ""}
              problem={problems[field.key] ?? null}
              tenantRows={rows[field.key]}
              onText={(text) => setText(field, text)}
              onNoLimit={(on) => setNoLimit(field, on)}
              onTenants={(next) => setTenants(field, next)}
              onRestore={() => restore(field)}
            />
          ))}
        </section>
      ))}
      <section className={card} data-group="review">
        <h2 className="text-base font-semibold">{t("settings.review.title")}</h2>
        <div className="mt-2">
          <ChangeList fields={fields} inForce={inForce} overrides={overrides} changed={changed} />
        </div>
        {preview && !reviewed && <p className="mt-2 text-xs text-warning">{t("settings.review.stale")}</p>}
        {reviewed && (
          <div className="mt-3 space-y-3">
            <ProblemList problems={preview.result.problems} />
            {preview.result.tree.length > 0 && <TimeoutTree pairs={preview.result.tree} previewing />}
          </div>
        )}
        <div className="mt-3 flex flex-wrap gap-2">
          <button type="button" className={quiet} disabled={busy || hasProblems || changed.length === 0} onClick={review}>
            {t("settings.review.open")}
          </button>
          <button
            type="button"
            className={button}
            disabled={busy || hasProblems || !reviewed || !preview.result.valid}
            onClick={save}
          >
            {t("settings.review.confirm")}
          </button>
          <button type="button" className={quiet} disabled={busy || changed.length === 0} onClick={() => reset(view)}>
            {t("settings.review.discard")}
          </button>
        </div>
      </section>
      <section className={card} data-group="capacity">
        <SectionHeading group="capacity" />
        <ReadOnlyList items={view.read_only} />
      </section>
      <section className={card} data-group="tree">
        <SectionHeading group="tree" />
        <TimeoutTree pairs={view.tree} />
      </section>
      <section className={card} data-group="usage">
        <SectionHeading group="usage" />
        <UsagePanel usage={usage} failed={usageFailed} updatedAt={usageAt} />
      </section>
      <section className={card} data-group="history">
        <SectionHeading group="history" />
        <VersionHistory
          versions={versions}
          current={view.version}
          confirming={confirming}
          busy={busy}
          onRevert={setConfirming}
          onConfirm={revert}
          onCancel={() => setConfirming(null)}
        />
      </section>
    </main>
  );
}
