"use client";

import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { AuthGate } from "@/components/AuthGate";
import { newUuid } from "@/lib/uuid";

/** Mints a conversation id and moves to it, only for a signed-in user. A signed-out visitor sees the sign-in
 * screen here, at /chat/new, and returns here after signing in; no id is minted before then (ADR-0441). */
function StartConversation() {
  const router = useRouter();
  const t = useTranslations("chat");

  useEffect(() => {
    router.replace(`/chat/${newUuid()}`);
  }, [router]);

  return <p className="p-8 text-center text-sm text-muted">{t("starting")}</p>;
}

export default function NewChatPage() {
  return (
    <AuthGate>
      <StartConversation />
    </AuthGate>
  );
}
