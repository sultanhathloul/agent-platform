import { NextIntlClientProvider } from "next-intl";
import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ar from "@/i18n/ar.json";
import en from "@/i18n/en.json";

export const CATALOGS = { ar, en } as const;
export type TestLocale = keyof typeof CATALOGS;

/** A component's static markup in `locale`, with the app's own catalogs (tests only). */
export function renderIn(locale: TestLocale, element: ReactElement): string {
  return renderToStaticMarkup(
    <NextIntlClientProvider locale={locale} messages={CATALOGS[locale]} timeZone="UTC">
      {element}
    </NextIntlClientProvider>,
  );
}
