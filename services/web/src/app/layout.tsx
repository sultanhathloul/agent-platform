import type { Metadata } from "next";
import { NextIntlClientProvider } from "next-intl";
import { getLocale, getMessages, getTranslations } from "next-intl/server";
import type { ReactNode } from "react";
import { AuthProvider } from "@/components/AuthProvider";
import { ConfigProvider } from "@/components/ConfigProvider";
import { Header } from "@/components/Header";
import { DEFAULT_LOCALE, directionOf, isLocale } from "@/lib/locale";
import { readRuntimeConfig } from "@/lib/runtime-config";
import { THEME_SCRIPT } from "@/lib/theme";
import "./globals.css";

// Runtime config and locale are read per request, never frozen at build time.
export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("app");
  return {
    title: t("name"),
    description: t("description"),
    // The brand's icons (ADR-0415): the .ico carries 16, 32 and 48 px; PNGs for browsers that prefer them.
    icons: {
      icon: [
        { url: "/brand/favicon.ico", sizes: "any" },
        { url: "/brand/favicon-32.png", sizes: "32x32", type: "image/png" },
        { url: "/brand/favicon-16.png", sizes: "16x16", type: "image/png" },
      ],
      apple: [{ url: "/brand/apple-touch-icon.png", sizes: "180x180" }],
    },
  };
}

export default async function RootLayout({ children }: { children: ReactNode }) {
  const raw = await getLocale();
  const locale = isLocale(raw) ? raw : DEFAULT_LOCALE;
  const messages = await getMessages();

  return (
    // data-theme is set by THEME_SCRIPT before React hydrates: it may differ from the server's HTML.
    <html lang={locale} dir={directionOf(locale)} suppressHydrationWarning>
      <head>
        {/* The remembered theme on <html> before the first paint: no flash of the other theme. */}
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="flex h-dvh flex-col bg-app font-sans text-fg antialiased">
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
