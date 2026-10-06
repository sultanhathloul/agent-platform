"use client";

import { useLocale, useTranslations } from "next-intl";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import type {
  PolicyDetail,
  PolicyImportReport,
  PolicyMetrics,
  PolicyTestResult,
  PolicyView,
} from "@/lib/api/types";
import { resolveErrorMessage, type ErrorInfo } from "@/lib/errors";
import {
  GatewayError,
  addPolicyVersion,
  createPolicy,
  exportPolicies,
  getPolicy,
  importPolicies,
  listPolicies,
  policyMetrics,
  setPolicyEnabled,
  testPolicy,
} from "@/lib/gateway";
import { DEFAULT_LOCALE, isLocale } from "@/lib/locale";
import {
  ACTIONS,
  CHECKERS,
  CHECKPOINTS,
  ON_ERROR,
  SEVERITIES,
  TARGETS,
  emptyForm,
  formFromPolicy,
  highlight,
  lastDays,
  matchesByPolicy,
  policyFromForm,
  type PolicyForm,
} from "@/lib/policies";
import { useAuth } from "../AuthProvider";
import { useRuntimeConfig } from "../ConfigProvider";

const METRIC_DAYS = 14;

/** `id@version`, how policies are named in events, traces and the audit log (A1). */
function policyRef(id: string, version: number | null | undefined): string {
  return [id, version ?? 1].join("@");
}
const card = "rounded-2xl bg-white p-4 shadow-sm ring-1 ring-slate-200";
const button =
  "rounded-md bg-emerald-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-800 disabled:opacity-50";
const quiet = "rounded-md px-3 py-1.5 text-sm text-slate-700 ring-1 ring-slate-300 hover:bg-slate-50 disabled:opacity-50";
const input = "w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm";

type Editing = { mode: "create" | "version"; form: PolicyForm } | null;

/** Guard policy administration (README §10.4): admin only; the gateway checks the role. */
export function PolicyAdmin() {
  const t = useTranslations();
  const rawLocale = useLocale();
  const locale = isLocale(rawLocale) ? rawLocale : DEFAULT_LOCALE;
  const { gatewayUrl } = useRuntimeConfig();
  const { getAccessToken, login } = useAuth();
  const pathname = usePathname();

  const [policies, setPolicies] = useState<PolicyView[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<PolicyDetail | null>(null);
  const [editing, setEditing] = useState<Editing>(null);
  const [metrics, setMetrics] = useState<PolicyMetrics | null>(null);
  const [error, setError] = useState<ErrorInfo | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

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

  const reload = useCallback(async () => {
    const listed = await authorized((token) => listPolicies({ gatewayUrl, token }));
    if (listed.ok) setPolicies(listed.value.policies);
    const counted = await authorized((token) => policyMetrics({ gatewayUrl, token, days: METRIC_DAYS }));
    if (counted.ok) setMetrics(counted.value);
  }, [authorized, gatewayUrl]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const open = useCallback(
    async (id: string) => {
      setSelected(id);
      setEditing(null);
      setError(null);
      const found = await authorized((token) => getPolicy({ gatewayUrl, token, id }));
      setDetail(found.ok ? found.value : null);
    },
    [authorized, gatewayUrl],
  );

  async function toggle(view: PolicyView) {
    setBusy(`toggle:${view.policy.id}`);
    setError(null);
    const enabled = !(view.policy.enabled ?? true);
    const result = await authorized((token) => setPolicyEnabled({ gatewayUrl, token, id: view.policy.id, enabled }));
    if (result.ok) {
      setPolicies((all) => all?.map((p) => (p.policy.id === view.policy.id ? result.value : p)) ?? null);
      if (selected === view.policy.id) await open(view.policy.id);
    }
    setBusy(null);
  }

  async function save() {
    if (!editing) return;
    const parsed = policyFromForm(editing.form);
    if (!parsed.ok) {
      setError({ code: "policy_invalid", details: [t("policies.configInvalid")] });
      return;
    }
    setBusy("save");
    setError(null);
    setNotice(null);
    const result = await authorized((token) =>
      editing.mode === "create"
        ? createPolicy({ gatewayUrl, token, policy: parsed.policy })
        : addPolicyVersion({ gatewayUrl, token, policy: parsed.policy }),
    );
    setBusy(null);
    if (!result.ok) return;
    setNotice(t("policies.saved", { version: result.value.policy.version ?? 1 }));
    setEditing(null);
    await reload();
    await open(result.value.policy.id);
  }

  const current = detail?.current.policy;
  const editingForm = editing?.form;

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-4 p-4">
      <header>
        <h1 className="text-xl font-semibold text-slate-800">{t("policies.title")}</h1>
        <p className="mt-1 text-sm text-slate-600">{t("policies.description")}</p>
      </header>

      {error && (
        <div role="alert" className="rounded-md bg-red-50 p-3 text-sm text-red-800 ring-1 ring-red-200">
          <p>{resolveErrorMessage(error, locale, (key) => t(key))}</p>
          {error.details && error.details.length > 0 && (
            <ul className="mt-1 list-disc ps-5">
              {error.details.map((d) => (
                <li key={d}>
                  <bdi dir="auto">{d}</bdi>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {notice && (
        <p role="status" className="rounded-md bg-emerald-50 p-3 text-sm text-emerald-800 ring-1 ring-emerald-200">
          {notice}
        </p>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <section className={card} aria-labelledby="policy-list">
          <div className="mb-3 flex items-center justify-between gap-2">
            <h2 id="policy-list" className="font-semibold text-slate-800">
              {t("policies.listTitle")}
            </h2>
            <button
              type="button"
              className={button}
              onClick={() => {
                setSelected(null);
                setDetail(null);
                setEditing({ mode: "create", form: emptyForm() });
              }}
            >
              {t("policies.newPolicy")}
            </button>
          </div>
          {policies === null ? (
            <p className="text-sm text-slate-500">{t("policies.loading")}</p>
          ) : policies.length === 0 ? (
            <p className="text-sm text-slate-500">{t("policies.empty")}</p>
          ) : (
            <ul className="divide-y divide-slate-100">
              {policies.map((view) => {
                const p = view.policy;
                const on = p.enabled ?? true;
                return (
                  <li key={p.id} className="flex items-start justify-between gap-3 py-2">
                    <button
                      type="button"
                      onClick={() => void open(p.id)}
                      className={`min-w-0 flex-1 text-start ${selected === p.id ? "text-emerald-800" : "text-slate-800"}`}
                    >
                      <span dir="auto" className="block truncate font-medium">
                        {p.name}
                      </span>
                      <span className="mt-0.5 flex flex-wrap gap-1 text-[11px] text-slate-500">
                        <bdi className="font-mono">{policyRef(p.id, p.version)}</bdi>
                        <span>·</span>
                        <bdi className="font-mono">{p.checker}</bdi>
                        <span>·</span>
                        <bdi className="font-mono">{p.checkpoints.join(", ")}</bdi>
                        <span>·</span>
                        <bdi className="font-mono">{p.scope}</bdi>
                        <span>·</span>
                        <span>{t.has(`guardActions.${p.action}`) ? t(`guardActions.${p.action}`) : p.action}</span>
                      </span>
                    </button>
                    <label className="flex shrink-0 items-center gap-1.5 text-xs text-slate-600">
                      <input
                        type="checkbox"
                        checked={on}
                        disabled={busy === `toggle:${p.id}`}
                        onChange={() => void toggle(view)}
                        aria-label={t("policies.toggleLabel", { name: p.name })}
                      />
                      {on ? t("policies.enabled") : t("policies.disabled")}
                    </label>
                  </li>
                );
              })}
            </ul>
          )}
          <ImportExport authorized={authorized} gatewayUrl={gatewayUrl} onImported={reload} />
        </section>

        <div className="flex min-w-0 flex-col gap-4">
          {editingForm && editing ? (
            <PolicyEditor
              form={editingForm}
              mode={editing.mode}
              busy={busy === "save"}
              onChange={(form) => setEditing({ ...editing, form })}
              onSave={() => void save()}
              onCancel={() => setEditing(null)}
            />
          ) : current ? (
            <section className={card} aria-labelledby="policy-detail">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h2 id="policy-detail" dir="auto" className="font-semibold text-slate-800">
                  {current.name}{" "}
                  <bdi className="font-mono text-xs text-slate-500">{policyRef(current.id, current.version)}</bdi>
                </h2>
                <button
                  type="button"
                  className={quiet}
                  onClick={() => setEditing({ mode: "version", form: formFromPolicy(current) })}
                >
                  {t("policies.edit")}
                </button>
              </div>
              {current.description && (
                <p dir="auto" className="mt-1 text-sm text-slate-600">
                  {current.description}
                </p>
              )}
              <pre dir="ltr" className="mt-3 max-h-64 overflow-auto rounded-md bg-slate-50 p-2 text-xs text-slate-700">
                {JSON.stringify(current.config ?? {}, null, 2)}
              </pre>
            </section>
          ) : (
            <section className={card}>
              <p className="text-sm text-slate-500">{t("policies.select")}</p>
            </section>
          )}

          {(current || editingForm) && (
            <TestPanel
              key={`${selected ?? "draft"}:${editing?.mode ?? "view"}`}
              authorized={authorized}
              gatewayUrl={gatewayUrl}
              policyId={editing ? null : (current?.id ?? null)}
              form={editingForm ?? (current ? formFromPolicy(current) : null)}
            />
          )}

          {detail && !editing && (
            <History detail={detail} onRestore={(form) => setEditing({ mode: "version", form })} />
          )}
        </div>
      </div>

      <MatchesPerDay metrics={metrics} />
    </div>
  );
}

type Authorized = <T>(call: (token: string) => Promise<T>) => Promise<{ ok: true; value: T } | { ok: false }>;

function PolicyEditor({
  form,
  mode,
  busy,
  onChange,
  onSave,
  onCancel,
}: {
  form: PolicyForm;
  mode: "create" | "version";
  busy: boolean;
  onChange: (form: PolicyForm) => void;
  onSave: () => void;
  onCancel: () => void;
}) {
  const t = useTranslations();
  const set = <K extends keyof PolicyForm>(key: K, value: PolicyForm[K]) => onChange({ ...form, [key]: value });
  const field = "flex flex-col gap-1 text-sm text-slate-700";

  return (
    <section className={card} aria-labelledby="policy-editor">
      <h2 id="policy-editor" className="mb-3 font-semibold text-slate-800">
        {mode === "create" ? t("policies.createTitle") : t("policies.versionTitle", { id: form.id })}
      </h2>
      <form
        className="grid gap-3 sm:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault();
          onSave();
        }}
      >
        <label className={field}>
          {t("policies.fields.id")}
          <input
            dir="ltr"
            className={`${input} font-mono`}
            value={form.id}
            disabled={mode === "version"}
            onChange={(e) => set("id", e.target.value)}
            required
          />
        </label>
        <label className={field}>
          {t("policies.fields.name")}
          <input dir="auto" className={input} value={form.name} onChange={(e) => set("name", e.target.value)} required />
        </label>
        <label className={`${field} sm:col-span-2`}>
          {t("policies.fields.description")}
          <textarea
            dir="auto"
            rows={2}
            className={input}
            value={form.description ?? ""}
            onChange={(e) => set("description", e.target.value)}
          />
        </label>
        <label className={field}>
          {t("policies.fields.scope")}
          <input dir="ltr" className={`${input} font-mono`} value={form.scope} onChange={(e) => set("scope", e.target.value)} />
          <span className="text-[11px] text-slate-500">{t("policies.fields.scopeHint")}</span>
        </label>
        <label className={field}>
          {t("policies.fields.group")}
          <input
            dir="ltr"
            className={`${input} font-mono`}
            value={form.group ?? ""}
            onChange={(e) => set("group", e.target.value)}
          />
          <span className="text-[11px] text-slate-500">{t("policies.fields.groupHint")}</span>
        </label>
        <fieldset className={`${field} sm:col-span-2`}>
          <legend>{t("policies.fields.checkpoints")}</legend>
          <div className="flex flex-wrap gap-3">
            {CHECKPOINTS.map((c) => (
              <label key={c} className="flex items-center gap-1 text-xs">
                <input
                  type="checkbox"
                  checked={form.checkpoints.includes(c)}
                  onChange={(e) =>
                    set("checkpoints", e.target.checked ? [...form.checkpoints, c] : form.checkpoints.filter((x) => x !== c))
                  }
                />
                <bdi className="font-mono">{c}</bdi>
                <span className="text-slate-500">{t(`policies.checkpoints.${c}`)}</span>
              </label>
            ))}
          </div>
        </fieldset>
        <label className={field}>
          {t("policies.fields.checker")}
          <select className={input} value={form.checker} onChange={(e) => set("checker", e.target.value)}>
            {CHECKERS.map((c) => (
              <option key={c} value={c}>
                {t(`policies.checkers.${c}`)}
              </option>
            ))}
          </select>
        </label>
        <label className={field}>
          {t("policies.fields.action")}
          <select className={input} value={form.action} onChange={(e) => set("action", e.target.value)}>
            {ACTIONS.map((a) => (
              <option key={a} value={a}>
                {t(`guardActions.${a}`)}
              </option>
            ))}
          </select>
        </label>
        <label className={field}>
          {t("policies.fields.severity")}
          <select className={input} value={form.severity ?? "medium"} onChange={(e) => set("severity", e.target.value)}>
            {SEVERITIES.map((s) => (
              <option key={s} value={s}>
                {t(`policies.severities.${s}`)}
              </option>
            ))}
          </select>
        </label>
        <label className={field}>
          {t("policies.fields.onError")}
          <select className={input} value={form.on_error ?? ""} onChange={(e) => set("on_error", e.target.value)}>
            {ON_ERROR.map((o) => (
              <option key={o || "default"} value={o}>
                {t(`policies.onError.${o || "default"}`)}
              </option>
            ))}
          </select>
        </label>
        <label className={field}>
          {t("policies.fields.messageAr")}
          <input dir="rtl" className={input} value={form.message_ar ?? ""} onChange={(e) => set("message_ar", e.target.value)} />
        </label>
        <label className={field}>
          {t("policies.fields.messageEn")}
          <input dir="ltr" className={input} value={form.message_en ?? ""} onChange={(e) => set("message_en", e.target.value)} />
        </label>
        <label className={`${field} sm:col-span-2`}>
          {t("policies.fields.config")}
          <textarea
            dir="ltr"
            rows={8}
            spellCheck={false}
            className={`${input} font-mono text-xs`}
            value={form.configText}
            onChange={(e) => set("configText", e.target.value)}
          />
          <span className="text-[11px] text-slate-500">{t("policies.fields.configHint")}</span>
        </label>
        <label className="flex items-center gap-2 text-sm text-slate-700">
          <input type="checkbox" checked={form.enabled ?? true} onChange={(e) => set("enabled", e.target.checked)} />
          {t("policies.fields.enabled")}
        </label>
        <div className="flex justify-end gap-2 sm:col-span-2">
          <button type="button" className={quiet} onClick={onCancel}>
            {t("policies.cancel")}
          </button>
          <button type="submit" className={button} disabled={busy}>
            {busy ? t("policies.saving") : mode === "create" ? t("policies.create") : t("policies.saveVersion")}
          </button>
        </div>
      </form>
    </section>
  );
}

function TestPanel({
  authorized,
  gatewayUrl,
  policyId,
  form,
}: {
  authorized: Authorized;
  gatewayUrl: string;
  policyId: string | null;
  form: PolicyForm | null;
}) {
  const t = useTranslations();
  const checkpoints = form?.checkpoints.length ? form.checkpoints : ["INPUT"];
  const [text, setText] = useState("");
  const [checkpoint, setCheckpoint] = useState<string>(checkpoints[0] ?? "INPUT");
  const [target, setTarget] = useState<string>("answer");
  const [result, setResult] = useState<PolicyTestResult | null>(null);
  const [tested, setTested] = useState("");
  const [running, setRunning] = useState(false);

  async function run() {
    if (!form || !text.trim()) return;
    let request;
    if (policyId) {
      request = { policy_id: policyId, text, checkpoint, target: checkpoint === "DATA" ? target : undefined };
    } else {
      const parsed = policyFromForm(form);
      if (!parsed.ok) return;
      request = { policy: parsed.policy, text, checkpoint, target: checkpoint === "DATA" ? target : undefined };
    }
    setRunning(true);
    const outcome = await authorized((token) => testPolicy({ gatewayUrl, token, request }));
    setRunning(false);
    if (outcome.ok) {
      setResult(outcome.value);
      setTested(text);
    }
  }

  const segments = useMemo(() => (result ? highlight(tested, result.spans) : []), [result, tested]);

  return (
    <section className={card} aria-labelledby="policy-test">
      <h2 id="policy-test" className="font-semibold text-slate-800">
        {t("policies.test.title")}
      </h2>
      <p className="mt-1 text-xs text-slate-500">{policyId ? t("policies.test.storedNote") : t("policies.test.draftNote")}</p>
      <textarea
        dir="auto"
        rows={4}
        className={`${input} mt-2`}
        value={text}
        placeholder={t("policies.test.placeholder")}
        aria-label={t("policies.test.text")}
        onChange={(e) => setText(e.target.value)}
      />
      <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
        <label className="flex items-center gap-1">
          {t("policies.test.checkpoint")}
          <select className="rounded-md border border-slate-300 px-2 py-1" value={checkpoint} onChange={(e) => setCheckpoint(e.target.value)}>
            {checkpoints.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        {checkpoint === "DATA" && (
          <label className="flex items-center gap-1">
            {t("policies.test.target")}
            <select className="rounded-md border border-slate-300 px-2 py-1" value={target} onChange={(e) => setTarget(e.target.value)}>
              {TARGETS.map((x) => (
                <option key={x} value={x}>
                  {t(`policies.targets.${x}`)}
                </option>
              ))}
            </select>
          </label>
        )}
        <button type="button" className={button} disabled={running || !text.trim()} onClick={() => void run()}>
          {running ? t("policies.test.running") : t("policies.test.run")}
        </button>
      </div>

      {result && (
        <div className="mt-3 space-y-2 text-sm" aria-live="polite">
          <p className="flex flex-wrap gap-2">
            <span className={`rounded-full px-2 py-0.5 text-xs ${result.matched ? "bg-amber-100 text-amber-900" : "bg-slate-100 text-slate-700"}`}>
              {result.matched ? t("policies.test.matched") : t("policies.test.notMatched")}
            </span>
            <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-700">
              {t("policies.test.action")}{" "}
              {t.has(`guardActions.${result.action}`) ? t(`guardActions.${result.action}`) : result.action}
            </span>
            <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-700">
              {t("policies.test.score")} <bdi>{result.score.toFixed(2)}</bdi>
            </span>
            <bdi className="font-mono text-xs text-slate-500">{result.policy_ref}</bdi>
          </p>
          {result.explanation && (
            <p className="text-xs text-slate-600">
              {t("policies.test.explanation")} <bdi dir="auto">{result.explanation}</bdi>
            </p>
          )}
          {result.error && (
            <p className="text-xs text-red-700">
              {t("policies.test.error")} <bdi dir="ltr">{result.error}</bdi>
            </p>
          )}
          <div>
            <h3 className="text-xs font-semibold text-slate-500">{t("policies.test.spans")}</h3>
            <p dir="auto" className="mt-1 whitespace-pre-wrap rounded-md bg-slate-50 p-2 leading-7">
              {segments.map((segment, i) =>
                segment.entity ? (
                  <mark key={i} title={segment.entity} className="rounded bg-amber-200 px-0.5">
                    {segment.text}
                    <bdi className="ms-1 align-super font-mono text-[10px] text-amber-900">{segment.entity}</bdi>
                  </mark>
                ) : (
                  <span key={i}>{segment.text}</span>
                ),
              )}
            </p>
            {result.spans.length === 0 && <p className="mt-1 text-xs text-slate-500">{t("policies.test.noSpans")}</p>}
          </div>
          {result.output_text != null && result.output_text !== tested && (
            <div>
              <h3 className="text-xs font-semibold text-slate-500">{t("policies.test.output")}</h3>
              <p dir="auto" className="mt-1 whitespace-pre-wrap rounded-md bg-slate-50 p-2">
                {result.output_text}
              </p>
            </div>
          )}
          {(result.message_ar || result.message_en) && (
            <p className="text-xs text-slate-600">
              {t("policies.test.message")} <span dir="auto">{result.message_ar ?? result.message_en}</span>
            </p>
          )}
        </div>
      )}
    </section>
  );
}

function History({ detail, onRestore }: { detail: PolicyDetail; onRestore: (form: PolicyForm) => void }) {
  const t = useTranslations();
  const locale = useLocale();
  const [shown, setShown] = useState<number | null>(null);
  const currentVersion = detail.current.policy.version;
  return (
    <section className={card} aria-labelledby="policy-history">
      <h2 id="policy-history" className="font-semibold text-slate-800">
        {t("policies.history.title")}
      </h2>
      <ol className="mt-2 divide-y divide-slate-100">
        {detail.versions.map((v) => (
          <li key={v.policy.version} className="py-2 text-sm">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span>
                <bdi className="font-mono">{t("policies.history.version", { version: v.policy.version ?? 1 })}</bdi>
                {v.policy.version === currentVersion && (
                  <span className="ms-2 rounded-full bg-emerald-50 px-2 py-0.5 text-xs text-emerald-800">
                    {t("policies.history.current")}
                  </span>
                )}
                <span className="ms-2 text-xs text-slate-500">
                  {t("policies.history.by", {
                    user: v.created_by,
                    date: new Date(v.created_at).toLocaleString(locale === "ar" ? "ar-SA" : "en-GB"),
                  })}
                </span>
              </span>
              <span className="flex gap-2">
                <button type="button" className={quiet} onClick={() => setShown(shown === v.policy.version ? null : (v.policy.version ?? null))}>
                  {shown === v.policy.version ? t("policies.history.hide") : t("policies.history.view")}
                </button>
                {v.policy.version !== currentVersion && (
                  <button type="button" className={quiet} onClick={() => onRestore(formFromPolicy(v.policy))}>
                    {t("policies.history.restore")}
                  </button>
                )}
              </span>
            </div>
            {shown === v.policy.version && (
              <pre dir="ltr" className="mt-2 max-h-56 overflow-auto rounded-md bg-slate-50 p-2 text-xs text-slate-700">
                {JSON.stringify(v.policy, null, 2)}
              </pre>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}

function ImportExport({
  authorized,
  gatewayUrl,
  onImported,
}: {
  authorized: Authorized;
  gatewayUrl: string;
  onImported: () => Promise<void>;
}) {
  const t = useTranslations();
  const [yaml, setYaml] = useState("");
  const [report, setReport] = useState<PolicyImportReport | null>(null);
  const [busy, setBusy] = useState(false);

  async function download() {
    setBusy(true);
    const result = await authorized((token) => exportPolicies({ gatewayUrl, token }));
    setBusy(false);
    if (!result.ok) return;
    const url = URL.createObjectURL(new Blob([result.value.yaml], { type: "application/yaml" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "guard-policies.yaml";
    link.click();
    URL.revokeObjectURL(url);
  }

  async function upload() {
    if (!yaml.trim()) return;
    setBusy(true);
    setReport(null);
    const result = await authorized((token) => importPolicies({ gatewayUrl, token, yaml }));
    setBusy(false);
    if (result.ok) {
      setReport(result.value);
      await onImported();
    }
  }

  return (
    <div className="mt-4 border-t border-slate-100 pt-3">
      <h3 className="text-sm font-semibold text-slate-700">{t("policies.io.title")}</h3>
      <div className="mt-2 flex flex-wrap gap-2">
        <button type="button" className={quiet} disabled={busy} onClick={() => void download()}>
          {t("policies.io.export")}
        </button>
        <label className={`${quiet} cursor-pointer`}>
          {t("policies.io.file")}
          <input
            type="file"
            accept=".yaml,.yml,text/yaml"
            className="sr-only"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void file.text().then(setYaml);
            }}
          />
        </label>
      </div>
      <textarea
        dir="ltr"
        rows={4}
        spellCheck={false}
        className={`${input} mt-2 font-mono text-xs`}
        value={yaml}
        placeholder={t("policies.io.placeholder")}
        aria-label={t("policies.io.placeholder")}
        onChange={(e) => setYaml(e.target.value)}
      />
      <button type="button" className={`${button} mt-2`} disabled={busy || !yaml.trim()} onClick={() => void upload()}>
        {t("policies.io.import")}
      </button>
      {report && (
        <p role="status" className="mt-2 text-xs text-slate-600">
          {t("policies.io.report", {
            created: report.created.length,
            versioned: report.versioned.length,
            switched: report.switched.length,
            unchanged: report.unchanged.length,
          })}
        </p>
      )}
    </div>
  );
}

function MatchesPerDay({ metrics }: { metrics: PolicyMetrics | null }) {
  const t = useTranslations();
  const days = useMemo(() => lastDays(metrics?.days ?? METRIC_DAYS), [metrics?.days]);
  const grouped = useMemo(() => matchesByPolicy(metrics?.rows ?? []), [metrics?.rows]);
  return (
    <section className={card} aria-labelledby="policy-metrics">
      <h2 id="policy-metrics" className="font-semibold text-slate-800">
        {t("policies.metrics.title", { days: metrics?.days ?? METRIC_DAYS })}
      </h2>
      {grouped.length === 0 ? (
        <p className="mt-2 text-sm text-slate-500">{t("policies.metrics.empty")}</p>
      ) : (
        <div className="mt-2 overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-slate-500">
                <th className="py-1 text-start font-medium">{t("policies.metrics.policy")}</th>
                <th className="px-1 py-1 text-end font-medium">{t("policies.metrics.total")}</th>
                {days.map((d) => (
                  <th key={d} className="px-1 py-1 text-center font-normal">
                    <bdi>{d.slice(5)}</bdi>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {grouped.map((g) => (
                <tr key={g.policyId} className="border-t border-slate-100">
                  <td className="py-1">
                    <bdi className="font-mono">{g.policyId}</bdi>
                  </td>
                  <td className="px-1 py-1 text-end font-medium">{g.total}</td>
                  {days.map((d) => (
                    <td key={d} className={`px-1 py-1 text-center ${g.byDay[d] ? "text-slate-800" : "text-slate-300"}`}>
                      {g.byDay[d] ?? 0}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
