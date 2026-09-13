import { describe, expect, it } from "vitest";
import { safeHref, safeReturnPath } from "./return-path";
import { isUuid, newUuid } from "./uuid";

describe("safeReturnPath", () => {
  it("keeps same-origin paths", () => {
    expect(safeReturnPath("/chat/0b8f7c1e-8d5c-4d0e-9a57-1f9e2d3c4b5a")).toBe("/chat/0b8f7c1e-8d5c-4d0e-9a57-1f9e2d3c4b5a");
  });

  it.each([undefined, 42, "https://evil.example", "//evil.example", "/\\evil.example", "chat", "/auth/callback"])(
    "falls back for %s",
    (value) => {
      expect(safeReturnPath(value)).toBe("/chat/new");
    },
  );
});

describe("safeHref", () => {
  it("allows http and https", () => {
    expect(safeHref("https://moe.gov.sa/ar/page")).toBe("https://moe.gov.sa/ar/page");
    expect(safeHref("http://intranet.local/doc")).toBe("http://intranet.local/doc");
  });

  it.each(["javascript:alert(1)", "data:text/html,x", "not a url", "", null, undefined])("rejects %s", (value) => {
    expect(safeHref(value)).toBeNull();
  });
});

describe("uuid", () => {
  it("generates valid v4 ids", () => {
    const id = newUuid();
    expect(isUuid(id)).toBe(true);
    expect(id[14]).toBe("4");
  });

  it("rejects non-uuids", () => {
    expect(isUuid("new")).toBe(false);
    expect(isUuid("../etc")).toBe(false);
  });
});
