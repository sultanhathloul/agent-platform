"use client";

import { useTranslations } from "next-intl";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { type ReactNode, useEffect, useState } from "react";
import { getMe } from "@/lib/gateway";
import { useAuth } from "./AuthProvider";
import { useRuntimeConfig } from "./ConfigProvider";

export type AdminCheck = "checking" | "admin" | "denied" | "unavailable";

/**
 * What a signed-in user sees on an admin page, given the gateway's answer: the page only for an
 * admin. The gateway still checks the admin role on every admin call; this keeps a non-admin from
 * seeing the admin screen at all.
 */
export function AdminGateView({ check, children }: { check: AdminCheck; children?: ReactNode }) {
  const t = useTranslations("admin");
  if (check === "admin") return <>{children}</>;
  if (check === "checking") {
    return <p className="p-8 text-center text-sm text-slate-500">{t("checking")}</p>;
  }
  return (
    <div className="flex h-full items-center justify-center p-6">
      <section
        role="alert"
        className="w-full max-w-md rounded-2xl bg-white p-8 text-center shadow-sm ring-1 ring-slate-200"
      >
        <h1 className="text-xl font-semibold">{t(check === "denied" ? "deniedTitle" : "unavailableTitle")}</h1>
        <p className="mt-2 text-sm leading-6 text-slate-600">
          {t(check === "denied" ? "deniedDescription" : "unavailableDescription")}
        </p>
        <Link
          href="/chat/new"
          className="mt-6 inline-block w-full rounded-lg bg-emerald-700 px-4 py-2.5 font-medium text-white hover:bg-emerald-800"
        >
          {t("backToChat")}
        </Link>
      </section>
    </div>
  );
}

/** Renders children only for a user the gateway reports as admin (`/v1/me`). Use inside AuthGate. */
export function AdminGate({ children }: { children: ReactNode }) {
  const { getAccessToken, login } = useAuth();
  const { gatewayUrl } = useRuntimeConfig();
  const pathname = usePathname();
  const [check, setCheck] = useState<AdminCheck>("checking");

  useEffect(() => {
    let active = true;
    void (async () => {
      const token = await getAccessToken();
      if (!token) {
        await login(pathname);
        return;
      }
      const me = await getMe({ gatewayUrl, token }).catch(() => null);
      if (!active) return;
      setCheck(me === null ? "unavailable" : me.is_admin ? "admin" : "denied");
    })();
    return () => {
      active = false;
    };
  }, [getAccessToken, login, pathname, gatewayUrl]);

  return <AdminGateView check={check}>{children}</AdminGateView>;
}
