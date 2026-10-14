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
  "memory_not_found",
  "turn_in_flight",
  "service_busy",
  "input_blocked",
  "guard_unavailable",
  "policy_not_found",
  "policy_exists",
  "policy_invalid",
  "identity_unavailable",
  "attachment_not_found",
  "attachment_limit",
  "file_too_large",
  "too_many_pages",
  "upload_rate_limited",
  "too_many_uploads",
  "upload_timeout",
  "file_type_unsupported",
  "storage_unavailable",
  "unknown",
] as const;

export type KnownErrorCode = (typeof KNOWN_ERROR_CODES)[number];

export type ErrorInfo = {
  code: string;
  message_ar?: string | null;
  message_en?: string | null;
  retryable?: boolean | null;
  /** policy_id@version of a guard policy whose block ended the turn; its messages are the policy's own. */
  policy_id?: string | null;
  /** What is wrong, e.g. a policy's validation errors. */
  details?: string[] | null;
};

export function isKnownErrorCode(code: string): code is KnownErrorCode {
  return (KNOWN_ERROR_CODES as readonly string[]).includes(code);
}

/**
 * A guard block shows the policy's own message (README §10.1: block returns the policy's
 * message_ar/en). Otherwise the UI's own catalog wins; otherwise the server message for the
 * current locale; otherwise "unknown".
 */
export function resolveErrorMessage(
  error: ErrorInfo,
  locale: Locale,
  translate: (key: `errors.${KnownErrorCode}`) => string,
): string {
  const serverMessage = locale === "ar" ? error.message_ar : error.message_en;
  if (error.policy_id && serverMessage?.trim()) return serverMessage;
  if (isKnownErrorCode(error.code)) return translate(`errors.${error.code}`);
  return serverMessage?.trim() ? serverMessage : translate("errors.unknown");
}
