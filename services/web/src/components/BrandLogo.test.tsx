import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { renderIn } from "@/test/render";
import { BrandLogo } from "./BrandLogo";

const WEB = join(__dirname, "..", "..");
const CSS = readFileSync(join(WEB, "src", "app", "globals.css"), "utf8").replace(/\s+/g, " ");

/** Every source file of the web app, tests excluded. */
function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.(tsx?|css)$/.test(name) && !/\.test\.tsx?$/.test(name) ? [path] : [];
  });
}

describe("the header logo (ADR-0428)", () => {
  it("has a light and a dark image, each with its 512 px file as the 2x source, named in Arabic and English", () => {
    for (const [locale, alt] of [["ar", "شعار منصة الوكيل"], ["en", "Agent Platform logo"]] as const) {
      const html = renderIn(locale, <BrandLogo />);
      const images = [...html.matchAll(/<img [^>]*>/g)].map((m) => m[0]);
      expect(images).toHaveLength(2);
      expect(images[0]).toContain('src="/brand/logo-64.png"');
      expect(images[0]).toContain('srcSet="/brand/logo-64.png 1x, /brand/logo-512.png 2x"');
      expect(images[0]).toContain("brand-logo-light");
      expect(images[1]).toContain('src="/brand/logo-dark-64.png"');
      expect(images[1]).toContain('srcSet="/brand/logo-dark-64.png 1x, /brand/logo-dark-512.png 2x"');
      expect(images[1]).toContain("brand-logo-dark");
      for (const image of images) {
        expect(image).toContain(`alt="${alt}"`);
        expect(image).toContain('width="32"');
        expect(image).toContain('height="32"');
      }
    }
  });

  it("shows the dark logo only in dark mode, chosen or from the system, with the same selectors as the tokens", () => {
    expect(CSS).toContain(".brand-logo-dark { display: none; }");
    expect(CSS).toMatch(
      /@media \(prefers-color-scheme: dark\) \{ :root:not\(\[data-theme="light"\]\) \.brand-logo-light \{ display: none; \} :root:not\(\[data-theme="light"\]\) \.brand-logo-dark \{ display: block; \} \}/,
    );
    expect(CSS).toContain(':root[data-theme="dark"] .brand-logo-light { display: none; }');
    expect(CSS).toContain(':root[data-theme="dark"] .brand-logo-dark { display: block; }');
    // The tokens switch on the same selectors, so the logo and the colours never disagree.
    expect(CSS).toContain('@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { color-scheme: dark;');
    expect(CSS).toContain(':root[data-theme="dark"] { color-scheme: dark;');
  });

  it("references only brand files that exist, and the logo.svg is gone", () => {
    const named = new Set<string>();
    for (const file of sources(join(WEB, "src"))) {
      for (const m of readFileSync(file, "utf8").matchAll(/\/brand\/([\w.-]+)/g)) named.add(m[1]!);
    }
    expect([...named].sort()).toEqual([
      "apple-touch-icon.png",
      "favicon-16.png",
      "favicon-32.png",
      "favicon.ico",
      "logo-512.png",
      "logo-64.png",
      "logo-dark-512.png",
      "logo-dark-64.png",
    ]);
    for (const name of named) expect(existsSync(join(WEB, "public", "brand", name)), name).toBe(true);
    expect(existsSync(join(WEB, "public", "brand", "logo.svg"))).toBe(false);
  });
});
