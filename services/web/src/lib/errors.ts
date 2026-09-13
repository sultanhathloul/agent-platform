import type { Locale } from "./locale";

// Codes with a localized message in src/i18n/*.json under `errors.*`.
export const KNOWN_ERROR_CODES = [
  "unauthorized",
  "forbidden",
  "kb_forbidden",
  "conversation_forbidden",
  "invalid_request",
  "agent_unavailable",
  "agent_stream_interrupted",
  "audit_unavailable",
  "internal_error",
  "turn_failed",
  "capability_failed",
  "network_error",
  "not_found",
  "no_capability_available",
  "context_overflow",
  "model_unavailable",
  "validation_failed",
  "output_blocked",
  "data_blocked",
  "unknown",
] as const;

export type KnownErrorCode = (typeof KNOWN_ERROR_CODES)[number];

export type ErrorInfo = {
  code: string;
  message_ar?: string | null;
  message_en?: string | null;
  retryable?: boolean | null;
};

export function isKnownErrorCode(code: string): code is KnownErrorCode {
  return (KNOWN_ERROR_CODES as readonly string[]).includes(code);
}

/** The UI's own catalog wins; otherwise the server message for the current locale; otherwise "unknown". */
export function resolveErrorMessage(
  error: ErrorInfo,
  locale: Locale,
  translate: (key: `errors.${KnownErrorCode}`) => string,
): string {
  if (isKnownErrorCode(error.code)) return translate(`errors.${error.code}`);
  const serverMessage = locale === "ar" ? error.message_ar : error.message_en;
  return serverMessage?.trim() ? serverMessage : translate("errors.unknown");
}
