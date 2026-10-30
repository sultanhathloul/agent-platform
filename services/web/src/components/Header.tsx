"use client";

import { useTranslations } from "next-intl";
import Link from "next/link";
import { useAuth } from "./AuthProvider";
import { LocaleToggle } from "./LocaleToggle";
import { useIsAdmin } from "./MeProvider";
import { ThemeToggle } from "./ThemeToggle";

export function Header() {
  const t = useTranslations();
  const auth = useAuth();
  const signedIn = auth.status === "authenticated";
  // Shows the admin links only; the gateway checks the admin role on every admin call.
  const admin = useIsAdmin();

  return (
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line bg-surface px-4 py-2.5 shadow-sm">
      {/* The brand at the inline-start corner: top right in Arabic, top left in English (ADR-0415). */}
      <Link href="/chat/new" className="flex items-center gap-2.5 rounded-md text-lg font-semibold text-fg">
        {/* eslint-disable-next-line @next/next/no-img-element -- a fixed 64 px asset, no optimisation needed */}
        <img src="/brand/logo-64.png" alt={t("header.logoAlt")} width={32} height={32} className="h-8 w-8" />
        <span>{t("app.name")}</span>
      </Link>
      <nav className="flex items-center gap-2 text-sm">
        {signedIn && (
          <>
            <Link href="/chat/new" className="rounded-md px-2.5 py-1.5 text-fg hover:bg-surface-2">
              {t("header.newChat")}
            </Link>
            <Link href="/settings/memory" className="rounded-md px-2.5 py-1.5 text-fg hover:bg-surface-2">
              {t("header.memory")}
            </Link>
            {admin && (
              <Link href="/admin/policies" className="rounded-md px-2.5 py-1.5 text-fg hover:bg-surface-2">
                {t("header.policies")}
              </Link>
            )}
            {admin && (
              <Link href="/admin/kbs" className="rounded-md px-2.5 py-1.5 text-fg hover:bg-surface-2">
                {t("header.kbs")}
              </Link>
            )}
            {admin && (
              <Link href="/admin/settings" className="rounded-md px-2.5 py-1.5 text-fg hover:bg-surface-2">
                {t("header.settings")}
              </Link>
            )}
            <span dir="auto" className="px-1 text-muted">
              {auth.displayName}
            </span>
          </>
        )}
        <ThemeToggle />
        <LocaleToggle />
        {signedIn && (
          <button
            type="button"
            onClick={() => void auth.logout()}
            className="rounded-md px-2.5 py-1.5 text-fg hover:bg-surface-2"
          >
            {t("header.signOut")}
          </button>
        )}
      </nav>
    </header>
  );
}
