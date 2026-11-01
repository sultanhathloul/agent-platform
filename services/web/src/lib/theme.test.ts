import { afterEach, describe, expect, it, vi } from "vitest";
import ar from "@/i18n/ar.json";
import en from "@/i18n/en.json";
import { THEME_KEY, THEME_SCRIPT, applyTheme, currentTheme } from "./theme";

/** A document element, a window with a system preference, and storage: what the theme code touches. */
function browser(system: "light" | "dark", stored: string | null, storage = true) {
  const attributes = new Map<string, string>();
  const saved = new Map<string, string>(stored ? [[THEME_KEY, stored]] : []);
  const blocked = () => {
    throw new Error("storage blocked");
  };
  vi.stubGlobal("document", {
    documentElement: {
      getAttribute: (name: string) => attributes.get(name) ?? null,
      setAttribute: (name: string, value: string) => attributes.set(name, value),
    },
  });
  vi.stubGlobal("window", { matchMedia: (query: string) => ({ matches: query.includes("dark") && system === "dark" }) });
  vi.stubGlobal("localStorage", {
    getItem: storage ? (key: string) => saved.get(key) ?? null : blocked,
    setItem: storage ? (key: string, value: string) => saved.set(key, value) : blocked,
  });
  return { attributes, saved };
}

afterEach(() => vi.unstubAllGlobals());

describe("theme (ADR-0415)", () => {
  it("follows the system until a choice is made", () => {
    browser("dark", null);
    new Function(THEME_SCRIPT)();
    expect(currentTheme()).toBe("dark");
    browser("light", null);
    new Function(THEME_SCRIPT)();
    expect(currentTheme()).toBe("light");
  });

  it("applies the remembered choice before the first paint, whatever the system says", () => {
    const { attributes } = browser("dark", "light");
    new Function(THEME_SCRIPT)();
    expect(attributes.get("data-theme")).toBe("light");
    expect(currentTheme()).toBe("light");
  });

  it("ignores anything but light or dark in storage", () => {
    const { attributes } = browser("light", "purple");
    new Function(THEME_SCRIPT)();
    expect(attributes.has("data-theme")).toBe(false);
  });

  it("remembers the toggle's choice, and still applies it when storage is blocked", () => {
    const { attributes, saved } = browser("light", null);
    applyTheme("dark");
    expect(attributes.get("data-theme")).toBe("dark");
    expect(saved.get(THEME_KEY)).toBe("dark");
    const blocked = browser("light", null, false);
    expect(() => applyTheme("dark")).not.toThrow();
    expect(blocked.attributes.get("data-theme")).toBe("dark");
    expect(() => new Function(THEME_SCRIPT)()).not.toThrow();
  });

  it("names the logo and the theme switch in Arabic and English", () => {
    for (const messages of [ar, en]) {
      for (const key of ["logoAlt", "themeToDark", "themeToLight", "themeLabel"] as const) {
        expect(messages.header[key]).toBeTruthy();
      }
    }
    expect(ar.app.name).toBe("منصة الوكيل");
    expect(en.app.name).toBe("Agent Platform");
  });
});
