"use client";

import { useTranslations } from "next-intl";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { useAuth } from "./AuthProvider";

/** Renders children only for a signed-in user; otherwise a sign-in screen. */
export function AuthGate({ children }: { children: ReactNode }) {
  const auth = useAuth();
  const pathname = usePathname();
  const t = useTranslations("auth");

  if (auth.status === "loading") {
    return <p className="p-8 text-center text-sm text-muted">{t("checking")}</p>;
  }

  if (auth.status === "anonymous") {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <section className="w-full max-w-md rounded-2xl bg-surface p-8 text-center shadow-sm ring-1 ring-line">
          <h1 className="text-xl font-semibold">{t("title")}</h1>
          <p className="mt-2 text-sm leading-6 text-muted">{t("description")}</p>
          <button
            type="button"
            onClick={() => void auth.login(pathname)}
            disabled={!auth.ready}
            className="mt-6 w-full rounded-lg bg-primary px-4 py-2.5 font-medium text-on-primary hover:bg-primary-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus disabled:opacity-50"
          >
            {t("signIn")}
          </button>
        </section>
      </div>
    );
  }

  return <>{children}</>;
}
