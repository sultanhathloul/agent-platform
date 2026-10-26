"use client";

import { useTranslations } from "next-intl";
import Link from "next/link";
import { useEffect, useState } from "react";
import { getMe } from "@/lib/gateway";
import { useAuth } from "./AuthProvider";
import { useRuntimeConfig } from "./ConfigProvider";
import { LocaleToggle } from "./LocaleToggle";

export function Header() {
  const t = useTranslations();
  const auth = useAuth();
  const { gatewayUrl } = useRuntimeConfig();
  const signedIn = auth.status === "authenticated";
  const { getAccessToken } = auth;
  // Shows the admin link only; the gateway checks the admin role on every admin call.
  const [admin, setAdmin] = useState(false);

  useEffect(() => {
    if (!signedIn) return;
    let active = true;
    void (async () => {
      const token = await getAccessToken();
      if (!token) return;
      const me = await getMe({ gatewayUrl, token }).catch(() => null);
      if (active) setAdmin(Boolean(me?.is_admin));
    })();
    return () => {
      active = false;
    };
  }, [signedIn, getAccessToken, gatewayUrl]);

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
            <Link href="/settings/memory" className="rounded-md px-2.5 py-1.5 text-slate-700 hover:bg-slate-100">
              {t("header.memory")}
            </Link>
            {admin && (
              <Link href="/admin/policies" className="rounded-md px-2.5 py-1.5 text-slate-700 hover:bg-slate-100">
                {t("header.policies")}
              </Link>
            )}
            {admin && (
              <Link href="/admin/kbs" className="rounded-md px-2.5 py-1.5 text-slate-700 hover:bg-slate-100">
                {t("header.kbs")}
              </Link>
            )}
            {admin && (
              <Link href="/admin/settings" className="rounded-md px-2.5 py-1.5 text-slate-700 hover:bg-slate-100">
                {t("header.settings")}
              </Link>
            )}
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
