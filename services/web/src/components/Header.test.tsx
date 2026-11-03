import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderIn } from "@/test/render";
import NewChatPage from "@/app/chat/new/page";
import { Header } from "./Header";

// Signed out, the header's brand is no link and /chat/new mints no conversation id (ADR-0441).
const state = vi.hoisted(() => ({ status: "anonymous" as "anonymous" | "authenticated", replace: vi.fn() }));
vi.mock("./AuthProvider", () => ({
  useAuth: () => ({ status: state.status, ready: true, displayName: "Sara", login: async () => {}, logout: async () => {} }),
}));
vi.mock("./MeProvider", () => ({ useIsAdmin: () => false }));
vi.mock("next/navigation", () => ({ usePathname: () => "/chat/new", useRouter: () => ({ replace: state.replace, refresh: () => {} }) }));

beforeEach(() => {
  state.replace.mockReset();
});

describe("the header's brand", () => {
  it("is not a link when signed out, in Arabic and English", () => {
    state.status = "anonymous";
    for (const [locale, name] of [["ar", "منصة الوكيل"], ["en", "Agent Platform"]] as const) {
      const html = renderIn(locale, <Header />);
      expect(html).not.toContain('href="/chat/new"');
      expect(html).toContain(name);
      expect(html).toContain("brand-logo-light");
    }
  });

  it("links to a new conversation when signed in", () => {
    state.status = "authenticated";
    for (const locale of ["ar", "en"] as const) {
      const html = renderIn(locale, <Header />);
      const brand = html.slice(0, html.indexOf("<nav"));
      expect(brand).toContain('href="/chat/new"');
      expect(brand).toContain("brand-logo-light");
    }
  });
});

describe("/chat/new", () => {
  it("shows the sign-in screen to a signed-out visitor, in Arabic and English", () => {
    state.status = "anonymous";
    for (const [locale, signIn] of [["ar", "تسجيل الدخول"], ["en", "Sign in"]] as const) {
      const html = renderIn(locale, <NewChatPage />);
      expect(html).toContain(signIn);
    }
    expect(state.replace).not.toHaveBeenCalled();
  });

  it("starts a conversation for a signed-in user", () => {
    state.status = "authenticated";
    const html = renderIn("en", <NewChatPage />);
    expect(html).not.toContain("Sign in");
  });
});
