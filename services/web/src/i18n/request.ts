import { cookies } from "next/headers";
import { getRequestConfig } from "next-intl/server";
import { DEFAULT_LOCALE, LOCALE_COOKIE, isLocale } from "@/lib/locale";

// Locale comes from a cookie, not the URL (no i18n routing); Arabic is the default.
export default getRequestConfig(async () => {
  const stored = (await cookies()).get(LOCALE_COOKIE)?.value;
  const locale = isLocale(stored) ? stored : DEFAULT_LOCALE;
  return {
    locale,
    messages: (await import(`./${locale}.json`)).default,
  };
});
