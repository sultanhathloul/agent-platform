"use client";

import { useTranslations } from "next-intl";
import { AUTO_OUTPUT, isOutput, type OutputChoice } from "@/lib/reports";

/**
 * The composer's output picker (reports PR, D3): «نص» / «تقرير تفاعلي», "automatic" by default, in
 * which case nothing is sent and the platform decides. The choice is kept per conversation.
 */
export function OutputPicker({
  value,
  onChange,
  disabled = false,
}: {
  value: OutputChoice;
  onChange: (output: OutputChoice) => void;
  disabled?: boolean;
}) {
  const t = useTranslations();
  return (
    <div className="flex items-center gap-2 text-xs text-slate-600">
      <label htmlFor="output-picker">{t("chat.outputPicker")}</label>
      <select
        id="output-picker"
        value={value}
        onChange={(e) => onChange(isOutput(e.target.value) ? e.target.value : AUTO_OUTPUT)}
        disabled={disabled}
        title={value ? undefined : t("chat.outputAutoHint")}
        className="rounded-lg border border-slate-300 bg-white px-2 py-1 text-xs text-slate-700 focus:border-emerald-600 focus:outline-none disabled:opacity-40"
      >
        <option value={AUTO_OUTPUT}>{t("chat.outputAuto")}</option>
        <option value="text">{t("chat.outputText")}</option>
        <option value="report">{t("chat.outputReport")}</option>
      </select>
    </div>
  );
}
