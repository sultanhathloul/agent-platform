import type { Metadata } from "next";
import { NextIntlClientProvider } from "next-intl";
import { getLocale, getMessages, getTranslations } from "next-intl/server";
import type { ReactNode } from "react";
import { AuthProvider } from "@/components/AuthProvider";
import { ConfigProvider } from "@/components/ConfigProvider";
import { Header } from "@/components/Header";
import { DEFAULT_LOCALE, directionOf, isLocale } from "@/lib/locale";
import { readRuntimeConfig } from "@/lib/runtime-config";
import "./globals.css";

// Runtime config and locale are read per request, never frozen at build time.
export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("app");
  return { title: t("name"), description: t("description") };
}

export default async function RootLayout({ children }: { children: ReactNode }) {
  const raw = await getLocale();
  const locale = isLocale(raw) ? raw : DEFAULT_LOCALE;
  const messages = await getMessages();

  return (
    <html lang={locale} dir={directionOf(locale)}>
      <body className="flex h-dvh flex-col bg-slate-50 font-sans text-slate-900 antialiased">
        <NextIntlClientProvider locale={locale} messages={messages}>
          <ConfigProvider config={readRuntimeConfig()}>
            <AuthProvider>
              <Header />
              <main className="min-h-0 flex-1">{children}</main>
            </AuthProvider>
          </ConfigProvider>
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
