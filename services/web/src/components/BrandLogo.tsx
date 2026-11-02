import { useTranslations } from "next-intl";

/** The sizes the logo is shown at, in CSS pixels: the header's and the sign-in screen's (ADR-0429). */
const SIZES = { 32: "h-8 w-8", 64: "h-16 w-16" } as const;

/**
 * The platform logo (ADR-0428, ADR-0429): the green logo-64.png in light mode, the white logo-dark-64.png in
 * dark mode, each with its 512 px file as the 2× source for high-density screens. Both images are in the markup;
 * CSS (globals.css, `.brand-logo-*`) shows the one for <html data-theme>, or for the system preference when no
 * choice is stored, the same rules as the colour tokens. THEME_SCRIPT sets data-theme before the first paint,
 * so the right logo shows with no flash. Used by the header and the sign-in screen.
 */
export function BrandLogo({ size = 32 }: { size?: keyof typeof SIZES }) {
  const t = useTranslations("header");
  return (
    <>
      {/* eslint-disable-next-line @next/next/no-img-element -- fixed brand assets, no optimisation needed */}
      <img
        src="/brand/logo-64.png"
        srcSet="/brand/logo-64.png 1x, /brand/logo-512.png 2x"
        alt={t("logoAlt")}
        width={size}
        height={size}
        className={`brand-logo-light ${SIZES[size]}`}
      />
      {/* eslint-disable-next-line @next/next/no-img-element -- fixed brand assets, no optimisation needed */}
      <img
        src="/brand/logo-dark-64.png"
        srcSet="/brand/logo-dark-64.png 1x, /brand/logo-dark-512.png 2x"
        alt={t("logoAlt")}
        width={size}
        height={size}
        className={`brand-logo-dark ${SIZES[size]}`}
      />
    </>
  );
}
