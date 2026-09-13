const DEFAULT_PATH = "/chat/new";

/** Only same-origin absolute paths survive a login round-trip (no open redirects). */
export function safeReturnPath(value: unknown): string {
  if (typeof value !== "string") return DEFAULT_PATH;
  if (!value.startsWith("/") || value.startsWith("//") || value.startsWith("/\\")) return DEFAULT_PATH;
  if (value.startsWith("/auth/")) return DEFAULT_PATH;
  return value;
}

/** Citation links come from untrusted sources: only http(s) URLs become links. */
export function safeHref(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}
