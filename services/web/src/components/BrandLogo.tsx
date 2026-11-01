import { useTranslations } from "next-intl";

/**
 * The platform logo (ADR-0428): logo-64.png in light mode, logo-dark-64.png in dark mode, each with its 512 px
 * file as the 2× source for high-density screens. Both images are in the markup; CSS (globals.css, `.brand-logo-*`)
 * shows the one for <html data-theme>, or for the system preference when no choice is stored, the same rules
 * as the colour tokens. THEME_SCRIPT sets data-theme before the first paint, so the right logo shows with no flash.
 */
export function BrandLogo() {
  const t = useTranslations("header");
  return (
    <>
      {/* eslint-disable-next-line @next/next/no-img-element -- fixed brand assets, no optimisation needed */}
      <img
        src="/brand/logo-64.png"
        srcSet="/brand/logo-64.png 1x, /brand/logo-512.png 2x"
        alt={t("logoAlt")}
        width={32}
        height={32}
        className="brand-logo-light h-8 w-8"
      />
      {/* eslint-disable-next-line @next/next/no-img-element -- fixed brand assets, no optimisation needed */}
      <img
        src="/brand/logo-dark-64.png"
        srcSet="/brand/logo-dark-64.png 1x, /brand/logo-dark-512.png 2x"
        alt={t("logoAlt")}
        width={32}
        height={32}
        className="brand-logo-dark h-8 w-8"
      />
    </>
  );
}
