"use client";

import { useLocale, useTranslations } from "next-intl";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import type {
  PolicyChange,
  PolicyDetail,
  PolicyImportReport,
  PolicyMetrics,
  PolicyOverrides,
  PolicyTestResult,
  PolicyView,
  PolicyWarning,
} from "@/lib/api/types";
import { resolveErrorMessage, type ErrorInfo } from "@/lib/errors";
import {
  GatewayError,
  addPolicyVersion,
  createPolicy,
  exportPolicies,
  getPolicy,
  getPolicyChanges,
  importPolicies,
  listPolicies,
  listPolicyRoles,
  policyMetrics,
  previewOverrides,
  setPolicyEnabled,
  testPolicy,
} from "@/lib/gateway";
import { DEFAULT_LOCALE, isLocale } from "@/lib/locale";
import {
  ACTIONS,
  CHECKERS,
  CHECKPOINTS,
  ON_ERROR,
  PURPOSES,
  SEVERITIES,
  TARGETS,
  emptyForm,
  formatList,
  formFromPolicy,
  highlight,
  lastDays,
  matchesByPolicy,
  messageFor,
  parsePolicyRoles,
  policyDescription,
  policyFromForm,
  policyName,
  visibleMatchRows,
  type PolicyForm,
} from "@/lib/policies";
import { configProblems, configToText, parseConfig, starterConfig, type Config } from "@/lib/policyConfig";
import { useAuth } from "../AuthProvider";
import { useRuntimeConfig } from "../ConfigProvider";
import { ActorName } from "./ActorName";
import { ConfigEditor } from "./ConfigEditor";
import { ConfigSummary } from "./ConfigSummary";
import { PolicyList } from "./PolicyList";
import { PolicyMeta } from "./PolicyMeta";
import { GROUP_EXAMPLES, Hint, ScopeField } from "./ScopeField";
import { EntityName, Explanation, OutputHeading } from "./TestResultParts";

const METRIC_DAYS = 14;
// Role names are identifiers, the same in both locales: shown as an example under the field.
const POLICY_ROLES_EXAMPLE = "policy:pii-exempt policy:no-sql";

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
  const [preview, setPreview] = useState<PolicyOverrides | null>(null);
  const [realmRoles, setRealmRoles] = useState<string[] | null>(null);
  // "Show test policies" (ADR-0119) governs the list and the daily matches table (ADR-0123).
  const [showTest, setShowTest] = useState(false);

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

  // The realm's policy:* roles, for the scope field (ADR-0107). Quiet on failure: the form
  // then simply does not warn about unknown roles.
  useEffect(() => {
    let active = true;
    void (async () => {
      const token = await getAccessToken();
      if (!token) return;
      const roles = await listPolicyRoles({ gatewayUrl, token }).catch(() => null);
      if (active && roles) setRealmRoles(roles.roles);
    })();
    return () => {
      active = false;
    };
  }, [getAccessToken, gatewayUrl]);

  async function checkOverrides() {
    if (!editing) return;
    const parsed = policyFromForm(editing.form);
    if (!parsed.ok) {
      setError({ code: "policy_invalid", details: [t("policies.configInvalid")] });
      return;
    }
    setError(null);
    const result = await authorized((token) => previewOverrides({ gatewayUrl, token, policy: parsed.policy }));
    if (result.ok) setPreview(result.value);
  }

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
    // The editor never sends what it refuses (ADR-0123); the agent's validator decides the rest.
    const problems = configProblems(editing.form.checker, (parsed.policy.config ?? {}) as Config, editing.form.checkpoints);
    if (problems.length > 0) {
      setError({
        code: "policy_invalid",
        details: [t("policies.editor.blocked"), ...new Set(problems.map((p) => t(`policies.editor.errors.${p.key}`)))],
      });
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
    const overrides = result.value.overrides ?? [];
    setNotice(
      [
        t("policies.saved", { version: result.value.policy.version ?? 1 }),
        overrides.length
          ? t("policies.overrides.saved", { list: formatList(overrides, locale) })
          : t("policies.overrides.nothing"),
        // ADR-0115: saved, with a warning the admin must see.
        ...(result.value.warnings ?? []).map((w) =>
          t("policies.warnings.wholeTextCannotLift", { list: formatList(w.policies, locale) }),
        ),
      ].join(" "),
    );
    setPreview(null);
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
          ) : (
            <PolicyList
              policies={policies}
              selected={selected}
              busy={busy}
              showTest={showTest}
              onShowTest={setShowTest}
              onOpen={(id) => void open(id)}
              onToggle={(view) => void toggle(view)}
            />
          )}
          <ImportExport authorized={authorized} gatewayUrl={gatewayUrl} onImported={reload} />
        </section>

        <div className="flex min-w-0 flex-col gap-4">
          {editingForm && editing ? (
            <PolicyEditor
              form={editingForm}
              mode={editing.mode}
              busy={busy === "save"}
              preview={preview}
              realmRoles={realmRoles}
              onChange={(form) => {
                setPreview(null);
                setEditing({ ...editing, form });
              }}
              onPreview={() => void checkOverrides()}
              onSave={() => void save()}
              onCancel={() => setEditing(null)}
            />
          ) : current ? (
            <section className={card} aria-labelledby="policy-detail">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h2 id="policy-detail" dir="auto" className="min-w-0 break-words font-semibold text-slate-800">
                  {policyName(current, locale)}
                </h2>
                <button
                  type="button"
                  className={quiet}
                  onClick={() => setEditing({ mode: "version", form: formFromPolicy(current) })}
                >
                  {t("policies.edit")}
                </button>
              </div>
              <div className="mt-1">
                <PolicyMeta
                  policy={current}
                  exception={Boolean(detail?.current.exception)}
                  overrides={detail?.current.overrides ?? []}
                />
              </div>
              {policyDescription(current, locale) && (
                <p dir="auto" className="mt-2 text-sm text-slate-600">
                  {policyDescription(current, locale)}
                </p>
              )}
              <OverridesNote overrides={detail?.current.overrides ?? []} exception={Boolean(detail?.current.exception)} />
              <ConfigSummary
                key={`${current.id}@${current.version}`}
                checker={current.checker}
                config={(current.config ?? {}) as Config}
                checkpoints={current.checkpoints}
              />
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

          {detail && !editing && (
            <ChangeLog
              key={`${detail.current.policy.id}@${detail.current.policy.version}:${detail.current.updated_at}`}
              authorized={authorized}
              gatewayUrl={gatewayUrl}
              policyId={detail.current.policy.id}
            />
          )}
        </div>
      </div>

      <MatchesPerDay metrics={metrics} policies={policies} showTest={showTest} />
    </div>
  );
}

type Authorized = <T>(call: (token: string) => Promise<T>) => Promise<{ ok: true; value: T } | { ok: false }>;

/** What a version overrides (ADR-0105): the exact policies, or "overrides nothing". */
function OverridesNote({ overrides, exception }: { overrides: string[]; exception: boolean }) {
  const t = useTranslations();
  return (
    <p className={`mt-2 text-xs ${exception ? "text-amber-800" : "text-slate-600"}`}>
      {overrides.length === 0 ? (
        t("policies.overrides.nothing")
      ) : (
        <>
          {exception ? t("policies.overrides.exception") : t("policies.overrides.label")}{" "}
          {overrides.map((ref) => (
            <bdi key={ref} className="me-1 font-mono">
              {ref}
            </bdi>
          ))}
        </>
      )}
    </p>
  );
}

/** Save warnings (ADR-0115): saved anyway, but the admin must know. */
function WarningsNote({ warnings }: { warnings: PolicyWarning[] }) {
  const t = useTranslations();
  const rawLocale = useLocale();
  const locale = isLocale(rawLocale) ? rawLocale : DEFAULT_LOCALE;
  if (warnings.length === 0) return null;
  return (
    <ul className="mt-1 space-y-1 text-xs text-amber-800" role="status">
      {warnings.map((w) => (
        <li key={w.code}>{t("policies.warnings.wholeTextCannotLift", { list: formatList(w.policies, locale) })}</li>
      ))}
    </ul>
  );
}

function PolicyEditor({
  form,
  mode,
  busy,
  preview,
  realmRoles,
  onChange,
  onPreview,
  onSave,
  onCancel,
}: {
  form: PolicyForm;
  mode: "create" | "version";
  busy: boolean;
  preview: PolicyOverrides | null;
  realmRoles: string[] | null;
  onChange: (form: PolicyForm) => void;
  onPreview: () => void;
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
          {t("policies.fields.purpose")}
          <select className={input} value={form.purpose ?? "production"} onChange={(e) => set("purpose", e.target.value as PolicyForm["purpose"])}>
            {PURPOSES.map((purpose) => (
              <option key={purpose} value={purpose}>
                {t(`policies.purposes.${purpose}`)}
              </option>
            ))}
          </select>
        </label>
        <label className={field}>
          {t("policies.fields.nameAr")}
          <input dir="rtl" lang="ar" className={input} value={form.name_ar} onChange={(e) => set("name_ar", e.target.value)} required />
        </label>
        <label className={field}>
          {t("policies.fields.nameEn")}
          <input dir="ltr" lang="en" className={input} value={form.name_en} onChange={(e) => set("name_en", e.target.value)} required />
        </label>
        <label className={field}>
          {t("policies.fields.descriptionAr")}
          <textarea
            dir="rtl"
            lang="ar"
            rows={3}
            className={input}
            value={form.description_ar ?? ""}
            onChange={(e) => set("description_ar", e.target.value)}
          />
        </label>
        <label className={field}>
          {t("policies.fields.descriptionEn")}
          <textarea
            dir="ltr"
            lang="en"
            rows={3}
            className={input}
            value={form.description_en ?? ""}
            onChange={(e) => set("description_en", e.target.value)}
          />
        </label>
        <ScopeField scope={form.scope} realmRoles={realmRoles} onChange={(scope) => set("scope", scope)} />
        <label className={field}>
          {t("policies.fields.group")}
          <input
            dir="ltr"
            className={`${input} font-mono`}
            value={form.group ?? ""}
            onChange={(e) => set("group", e.target.value)}
          />
          <Hint text={t("policies.fields.groupHint")} examples={GROUP_EXAMPLES} />
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
          <select
            className={input}
            value={form.checker}
            onChange={(e) => {
              // Another checker reads another config: start from its template, keeping DATA targets.
              const previous = parseConfig(form.configText);
              const config = starterConfig(e.target.value, previous.ok ? previous.config : {});
              onChange({ ...form, checker: e.target.value, configText: configToText(config) });
            }}
          >
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
        <ConfigEditor
          checker={form.checker}
          configText={form.configText}
          checkpoints={form.checkpoints}
          onChange={(configText) => set("configText", configText)}
        />
        <label className="flex items-center gap-2 text-sm text-slate-700">
          <input type="checkbox" checked={form.enabled ?? true} onChange={(e) => set("enabled", e.target.checked)} />
          {t("policies.fields.enabled")}
        </label>
        <div className="sm:col-span-2">
          <button type="button" className={quiet} onClick={onPreview}>
            {t("policies.overrides.check")}
          </button>
          {preview && <OverridesNote overrides={preview.overrides} exception={preview.exception} />}
          {preview && <WarningsNote warnings={preview.warnings ?? []} />}
        </div>
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
  const rawLocale = useLocale();
  const locale = isLocale(rawLocale) ? rawLocale : DEFAULT_LOCALE;
  const checkpoints = form?.checkpoints.length ? form.checkpoints : ["INPUT"];
  const [text, setText] = useState("");
  const [tenant, setTenant] = useState("default");
  const [roles, setRoles] = useState("");
  const [checkpoint, setCheckpoint] = useState<string>(checkpoints[0] ?? "INPUT");
  const [target, setTarget] = useState<string>("answer");
  const [result, setResult] = useState<PolicyTestResult | null>(null);
  const [tested, setTested] = useState("");
  const [running, setRunning] = useState(false);

  async function run() {
    if (!form || !text.trim()) return;
    // Whom to evaluate for: a tenant and policy roles (README §10.4, ADR-0108).
    const principal = { tenant_id: tenant.trim() || "default", policy_roles: parsePolicyRoles(roles) };
    const common = { text, checkpoint, target: checkpoint === "DATA" ? target : undefined, principal };
    let request;
    if (policyId) {
      request = { policy_id: policyId, ...common };
    } else {
      const parsed = policyFromForm(form);
      if (!parsed.ok) return;
      request = { policy: parsed.policy, ...common };
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
      <fieldset className="mt-2 flex flex-wrap items-center gap-2 text-sm">
        <legend className="text-xs text-slate-500">{t("policies.test.principal")}</legend>
        <label className="flex items-center gap-1">
          {t("policies.test.tenant")}
          <input dir="ltr" className="rounded-md border border-slate-300 px-2 py-1 font-mono" value={tenant} onChange={(e) => setTenant(e.target.value)} />
        </label>
        <label className="flex min-w-0 flex-1 flex-wrap items-center gap-1">
          {t("policies.test.roles")}
          <input
            dir="ltr"
            className="min-w-0 flex-1 rounded-md border border-slate-300 px-2 py-1 font-mono placeholder:font-sans"
            value={roles}
            placeholder={t("policies.test.rolesHint")}
            aria-describedby="policy-roles-example"
            onChange={(e) => setRoles(e.target.value)}
          />
          <span id="policy-roles-example" className="basis-full text-[11px] text-slate-500">
            {t("policies.test.rolesExample")}{" "}
            <bdi dir="ltr" className="font-mono">
              {POLICY_ROLES_EXAMPLE}
            </bdi>
          </span>
        </label>
      </fieldset>

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
          {(result.explanation_key || result.explanation) && (
            <p className="text-xs text-slate-600">
              {t("policies.test.explanation")}{" "}
              <Explanation explanationKey={result.explanation_key} args={result.explanation_args} fallback={result.explanation} />
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
                    <EntityName entity={segment.entity} className="ms-1 align-super text-[10px] text-amber-900" />
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
              <OutputHeading action={result.action} />
              <p dir="auto" className="mt-1 whitespace-pre-wrap rounded-md bg-slate-50 p-2">
                {result.output_text}
              </p>
            </div>
          )}
          {messageFor(result, locale) && (
            <p className="text-xs text-slate-600">
              {t("policies.test.message")} <span dir="auto">{messageFor(result, locale)}</span>
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
                {/* A label that holds Arabic: the sans face, never monospace (ADR-0119). */}
                <span className="font-medium">{t("policies.history.version", { version: v.policy.version ?? 1 })}</span>
                {v.policy.version === currentVersion && (
                  <span className="ms-2 rounded-full bg-emerald-50 px-2 py-0.5 text-xs text-emerald-800">
                    {t("policies.history.current")}
                  </span>
                )}
                <span className="ms-2 text-xs text-slate-500">
                  {t.rich("policies.history.by", {
                    user: v.created_by,
                    date: new Date(v.created_at).toLocaleString(locale === "ar" ? "ar-SA" : "en-GB"),
                    who: () => <ActorName id={v.created_by} username={v.created_by_username} name={v.created_by_name} />,
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
            <div className="mt-1">
              <PolicyMeta policy={v.policy} exception={Boolean(v.exception)} overrides={v.overrides ?? []} />
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

/** The policy's change log (ADR-0108): every save, switch, import and seed, with its actor. */
function ChangeLog({ authorized, gatewayUrl, policyId }: { authorized: Authorized; gatewayUrl: string; policyId: string }) {
  const t = useTranslations();
  const rawLocale = useLocale();
  const locale = isLocale(rawLocale) ? rawLocale : DEFAULT_LOCALE;
  const [changes, setChanges] = useState<PolicyChange[] | null>(null);

  useEffect(() => {
    let live = true;
    void authorized((token) => getPolicyChanges({ gatewayUrl, token, id: policyId })).then((result) => {
      if (live && result.ok) setChanges(result.value.changes);
    });
    return () => {
      live = false;
    };
  }, [authorized, gatewayUrl, policyId]);

  return (
    <section className={card} aria-labelledby="policy-changes">
      <h2 id="policy-changes" className="font-semibold text-slate-800">
        {t("policies.changes.title")}
      </h2>
      {changes !== null && changes.length === 0 && <p className="mt-2 text-sm text-slate-500">{t("policies.changes.empty")}</p>}
      <ol className="mt-2 divide-y divide-slate-100">
        {(changes ?? []).map((c, i) => (
          <li key={`${c.changed_at}-${i}`} className="flex flex-wrap items-center gap-2 py-2 text-sm">
            <span>{t.has(`policies.changes.kinds.${c.change}`) ? t(`policies.changes.kinds.${c.change}`) : c.change}</span>
            <bdi className="font-mono text-xs">{c.policy_ref}</bdi>
            {c.exception && (
              <span className="rounded-full bg-amber-100 px-1.5 text-xs text-amber-900" title={formatList(c.overrides, locale)}>
                {t("policies.changes.exception")}
              </span>
            )}
            {(c.induced ?? []).length > 0 && (
              <span className="text-xs text-slate-600">
                {t("policies.changes.induced", {
                  list: formatList(
                    (c.induced ?? []).map((i) =>
                      t(i.exception ? "policies.changes.inducedFlagged" : "policies.changes.inducedUnflagged", {
                        policy: i.policy_ref,
                      }),
                    ),
                    locale,
                  ),
                })}
              </span>
            )}
            <span className="text-xs text-slate-500">
              {t.rich("policies.changes.by", {
                user: c.actor,
                date: new Date(c.changed_at).toLocaleString(locale === "ar" ? "ar-SA" : "en-GB"),
                who: () => <ActorName id={c.actor} username={c.actor_username} name={c.actor_name} />,
              })}
            </span>
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
      <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-700">
        {t("policies.io.title")}
        <bdi dir="ltr" className="rounded-full bg-slate-100 px-1.5 font-mono text-[11px] font-normal text-slate-600" data-chip="format">
          {t("policies.io.format")}
        </bdi>
      </h3>
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
        className={`${input} mt-2 font-mono text-xs placeholder:font-sans`}
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

export function MatchesPerDay({
  metrics,
  policies,
  showTest,
}: {
  metrics: PolicyMetrics | null;
  policies: PolicyView[] | null;
  showTest: boolean;
}) {
  const t = useTranslations();
  const days = useMemo(() => lastDays(metrics?.days ?? METRIC_DAYS), [metrics?.days]);
  const grouped = useMemo(
    () => matchesByPolicy(visibleMatchRows(metrics?.rows ?? [], policies, showTest)),
    [metrics?.rows, policies, showTest],
  );
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
                <tr key={g.policyId} className="border-t border-slate-100" data-match-row={g.policyId}>
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
