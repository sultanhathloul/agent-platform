"use client";

import { useLocale, useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useTransition } from "react";
import { LOCALE_COOKIE, type Locale } from "@/lib/locale";

export function LocaleToggle() {
  const locale = useLocale();
  const t = useTranslations();
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const other: Locale = locale === "ar" ? "en" : "ar";

  function switchLocale() {
    document.cookie = `${LOCALE_COOKIE}=${other}; path=/; max-age=31536000; samesite=lax`;
    // Server components re-render with the new locale; client state (the conversation) is kept.
    startTransition(() => router.refresh());
  }

  return (
    <button
      type="button"
      onClick={switchLocale}
      disabled={pending}
      aria-label={t("header.switchLanguage")}
      className="rounded-md px-2.5 py-1.5 text-fg ring-1 ring-line-strong hover:bg-surface-2 disabled:opacity-50"
    >
      <span lang={other}>{t(`locale.${other}`)}</span>
    </button>
  );
}
