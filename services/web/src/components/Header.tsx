"use client";

import { useTranslations } from "next-intl";
import Link from "next/link";
import { useAuth } from "./AuthProvider";
import { LocaleToggle } from "./LocaleToggle";

export function Header() {
  const t = useTranslations();
  const auth = useAuth();
  const signedIn = auth.status === "authenticated";

  return (
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-white px-4 py-3">
      <Link href="/chat/new" className="text-lg font-semibold text-emerald-800">
        {t("app.name")}
      </Link>
      <nav className="flex items-center gap-2 text-sm">
        {signedIn && (
          <>
            <Link href="/chat/new" className="rounded-md px-2.5 py-1.5 text-slate-700 hover:bg-slate-100">
              {t("header.newChat")}
            </Link>
            <span dir="auto" className="px-1 text-slate-600">
              {auth.displayName}
            </span>
          </>
        )}
        <LocaleToggle />
        {signedIn && (
          <button
            type="button"
            onClick={() => void auth.logout()}
            className="rounded-md px-2.5 py-1.5 text-slate-700 hover:bg-slate-100"
          >
            {t("header.signOut")}
          </button>
        )}
      </nav>
    </header>
  );
}
