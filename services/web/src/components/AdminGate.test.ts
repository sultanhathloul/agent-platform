import { createElement } from "react";
import { describe, expect, it } from "vitest";
import { renderIn } from "@/test/render";
import { type AdminCheck, AdminGateView } from "./AdminGate";

const SECRET = "admin-page-content";

function render(locale: "ar" | "en", check: AdminCheck): string {
  return renderIn(locale, createElement(AdminGateView, { check }, createElement("div", null, SECRET)));
}

describe("the admin page gate", () => {
  it("shows the admin page to an admin only", () => {
    expect(render("ar", "admin")).toContain(SECRET);
    for (const check of ["checking", "denied", "unavailable"] as const) {
      expect(render("ar", check)).not.toContain(SECRET);
      expect(render("en", check)).not.toContain(SECRET);
    }
  });

  it("tells a non-admin the page is for administrators, in both locales", () => {
    expect(render("ar", "denied")).toContain("للمشرفين فقط");
    expect(render("en", "denied")).toContain("Administrators only");
    expect(render("ar", "denied")).toContain('href="/chat/new"');
  });

  it("does not claim a denial when the gateway could not be reached", () => {
    expect(render("en", "unavailable")).toContain("Permissions could not be checked");
    expect(render("en", "unavailable")).not.toContain("Administrators only");
  });
});
