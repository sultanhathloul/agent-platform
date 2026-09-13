import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { FlatCompat } from "@eslint/eslintrc";

const compat = new FlatCompat({ baseDirectory: dirname(fileURLToPath(import.meta.url)) });

const config = [
  {
    ignores: ["node_modules/**", ".next/**", "out/**", "next-env.d.ts", "src/lib/api/*.d.ts"],
  },
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    // Every user-facing string lives in src/i18n/{ar,en}.json (CLAUDE.md rule 7).
    files: ["src/**/*.tsx"],
    rules: {
      "react/jsx-no-literals": ["error", { noStrings: true, ignoreProps: true, allowedStrings: ["·"] }],
    },
  },
];

export default config;
