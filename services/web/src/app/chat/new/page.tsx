"use client";

import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { newUuid } from "@/lib/uuid";

export default function NewChatPage() {
  const router = useRouter();
  const t = useTranslations("chat");

  useEffect(() => {
    router.replace(`/chat/${newUuid()}`);
  }, [router]);

  return <p className="p-8 text-center text-sm text-slate-500">{t("starting")}</p>;
}
