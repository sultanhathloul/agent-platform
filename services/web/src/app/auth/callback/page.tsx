"use client";

import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useAuth } from "@/components/AuthProvider";

export default function AuthCallbackPage() {
  const { ready, completeSignin, login } = useAuth();
  const router = useRouter();
  const t = useTranslations("auth");
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!ready) return;
    let active = true;
    completeSignin()
      .then((path) => {
        if (active) router.replace(path);
      })
      .catch(() => {
        if (active) setFailed(true);
      });
    return () => {
      active = false;
    };
  }, [ready, completeSignin, router]);

  if (!failed) return <p className="p-8 text-center text-sm text-muted">{t("completing")}</p>;

  return (
    <div className="p-8 text-center">
      <p role="alert" className="text-sm text-error">
        {t("callbackFailed")}
      </p>
      <button
        type="button"
        onClick={() => void login("/chat/new")}
        className="mt-4 rounded-lg bg-primary px-4 py-2 font-medium text-on-primary hover:bg-primary-hover"
      >
        {t("retry")}
      </button>
    </div>
  );
}
