import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// Arabic letters join; letter-spacing breaks the joins and a case transform means nothing to
// them. Any element can hold Arabic text in the ar locale, so neither is used anywhere (ADR-0119).
const FORBIDDEN = /\b(tracking-[\w[\]./-]+|uppercase|lowercase|capitalize|normal-case|letter-spacing|text-transform|font-variant)\b/;

function sources(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return sources(path);
    return /\.(tsx|ts|css)$/.test(entry.name) && !entry.name.endsWith(".test.ts") && !entry.name.endsWith(".d.ts")
      ? [path]
      : [];
  });
}

describe("Arabic shaping", () => {
  it("no source sets letter-spacing or a text transform", () => {
    const found = sources(join(__dirname, ".."))
      .flatMap((file) => readFileSync(file, "utf-8").split("\n").map((line, i) => ({ file, line: i + 1, text: line })))
      .filter(({ text }) => FORBIDDEN.test(text))
      .map(({ file, line, text }) => `${file}:${line}: ${text.trim()}`);
    expect(found).toEqual([]);
  });
});
