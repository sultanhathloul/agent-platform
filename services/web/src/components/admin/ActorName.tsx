"use client";

import { useTranslations } from "next-intl";
import { actorLabel } from "@/lib/policies";

/**
 * Who made a change (ADR-0123): the display name kept at the time, with the username and id in
 * a tooltip; a row saved before names were kept shows its id. The name is free text, so its
 * direction follows its own first strong character.
 */
export function ActorName({ id, username, name }: { id: string; username?: string | null; name?: string | null }) {
  const t = useTranslations();
  const actor = actorLabel({ id, username, name });
  const tooltip = actor.username
    ? t("policies.actor.tooltip", { username: actor.username, id: actor.id })
    : t("policies.actor.tooltipId", { id: actor.id });
  return (
    <bdi dir="auto" title={tooltip} data-actor="">
      {actor.label}
    </bdi>
  );
}
