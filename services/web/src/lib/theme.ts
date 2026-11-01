/**
 * Light and dark (owner decision on PR #17, ADR-0415). The default follows the system; the header's toggle
 * chooses one and the browser remembers it. The choice is applied as <html data-theme> by THEME_SCRIPT, an
 * inline script in the document head, before the first paint, so no page flashes the other theme.
 */

export type Theme = "light" | "dark";

export const THEME_KEY = "platform-theme";

/** Runs before the first paint (src/app/layout.tsx): the remembered choice, if any, on <html>. */
export const THEME_SCRIPT = `(function(){try{var t=localStorage.getItem("${THEME_KEY}");if(t==="light"||t==="dark")document.documentElement.setAttribute("data-theme",t);}catch(e){}})();`;

export function currentTheme(): Theme {
  const chosen = document.documentElement.getAttribute("data-theme");
  if (chosen === "light" || chosen === "dark") return chosen;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute("data-theme", theme);
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    // Storage blocked (a private window): the choice holds for this page only.
  }
}
