"use client";

import { useTranslations } from "next-intl";
import Link from "next/link";
import { useAuth } from "./AuthProvider";
import { BrandLogo } from "./BrandLogo";
import { LocaleToggle } from "./LocaleToggle";
import { useIsAdmin } from "./MeProvider";
import { ThemeToggle } from "./ThemeToggle";

const BRAND = "flex items-center gap-2.5 rounded-md text-lg font-semibold text-fg";

export function Header() {
  const t = useTranslations();
  const auth = useAuth();
  const signedIn = auth.status === "authenticated";
  // Shows the admin links only; the gateway checks the admin role on every admin call.
  const admin = useIsAdmin();

  return (
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line bg-surface px-4 py-2.5 shadow-sm">
      {/* The brand at the inline-start corner: top right in Arabic, top left in English (ADR-0415). A link to a
          new conversation only when signed in; signed out it is not a link, so a click mints no id (ADR-0441). */}
      {signedIn ? (
        <Link href="/chat/new" className={BRAND}>
          <BrandLogo />
          <span>{t("app.name")}</span>
        </Link>
      ) : (
        <div className={BRAND}>
          <BrandLogo />
          <span>{t("app.name")}</span>
        </div>
      )}
      <nav className="flex items-center gap-2 text-sm">
        {signedIn && (
          <>
            <Link href="/chat/new" className="rounded-md px-2.5 py-1.5 text-fg hover:bg-surface-2">
              {t("header.newChat")}
            </Link>
            <Link href="/settings/memory" className="rounded-md px-2.5 py-1.5 text-fg hover:bg-surface-2">
              {t("header.memory")}
            </Link>
            <Link href="/reports" className="rounded-md px-2.5 py-1.5 text-fg hover:bg-surface-2">
              {t("header.reports")}
            </Link>
            <Link href="/files" className="rounded-md px-2.5 py-1.5 text-fg hover:bg-surface-2">
              {t("header.files")}
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
