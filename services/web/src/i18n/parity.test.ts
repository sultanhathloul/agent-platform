import { describe, expect, it } from "vitest";
import ar from "./ar.json";
import en from "./en.json";

type Tree = { [key: string]: string | Tree };

function leaves(tree: Tree, prefix = ""): Map<string, string> {
  const out = new Map<string, string>();
  for (const [key, value] of Object.entries(tree)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof value === "string") out.set(path, value);
    else for (const [k, v] of leaves(value, path)) out.set(k, v);
  }
  return out;
}

// ICU arguments ({name}) and rich-text tags (<key>) must match across locales.
function placeholders(message: string): string[] {
  const args = [...message.matchAll(/\{(\w+)/g)].map((m) => `{${m[1]}}`);
  const tags = [...message.matchAll(/<\/?(\w+)>/g)].map((m) => m[0]);
  return [...args, ...tags].sort();
}

describe("i18n catalogs", () => {
  const arLeaves = leaves(ar as Tree);
  const enLeaves = leaves(en as Tree);

  it("have the same keys", () => {
    expect([...arLeaves.keys()].sort()).toEqual([...enLeaves.keys()].sort());
  });

  it("have no empty strings", () => {
    for (const [key, value] of [...arLeaves, ...enLeaves]) {
      expect(value.trim(), key).not.toBe("");
    }
  });

  it("use the same placeholders in both languages", () => {
    for (const [key, value] of arLeaves) {
      expect(placeholders(value), key).toEqual(placeholders(enLeaves.get(key) ?? ""));
    }
  });

  it("cover every error code the UI maps", async () => {
    const { KNOWN_ERROR_CODES } = await import("@/lib/errors");
    for (const code of KNOWN_ERROR_CODES) {
      expect(arLeaves.has(`errors.${code}`), code).toBe(true);
    }
  });
});
