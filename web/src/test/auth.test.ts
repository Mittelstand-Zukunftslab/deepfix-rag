import { describe, expect, it } from "vitest";

import { navigationItems } from "@/components/layout/navigation";
import { authRedirect } from "@/features/auth/guard";
import { apiUpstreamUrl, isApiPath } from "@/api-proxy";
import { credentialsSchema } from "@/schemas/auth";
import { fieldForAuthError } from "@/services/auth-service";

const member = { id: "1", email: "a@example.com", role: "member" as const };
const admin = { id: "2", email: "b@example.com", role: "admin" as const };

describe("credentials schema", () => {
  it("asks for an email and a password of at least 8 characters", () => {
    const empty = credentialsSchema.safeParse({ email: "  ", password: "short" });
    expect(empty.success).toBe(false);
    if (empty.success) return;
    expect(empty.error.formErrors.fieldErrors.email?.[0]).toBe("Enter an email address.");
    expect(empty.error.formErrors.fieldErrors.password?.[0]).toBe("Use at least 8 characters.");

    const invalid = credentialsSchema.safeParse({ email: "not-an-email", password: "longenough" });
    expect(invalid.success).toBe(false);
    if (invalid.success) return;
    expect(invalid.error.formErrors.fieldErrors.email?.[0]).toBe("Enter a valid email address.");
  });
});

describe("auth redirect", () => {
  it("sends signed-out visitors to sign in and signed-in visitors away from auth pages", () => {
    expect(authRedirect("/search", null)).toBe("/sign-in");
    expect(authRedirect("/", null)).toBe("/sign-in");
    expect(authRedirect("/sign-in", null)).toBeNull();
    expect(authRedirect("/register", null)).toBeNull();
    expect(authRedirect("/sign-in", member)).toBe("/search");
    expect(authRedirect("/register", member)).toBe("/search");
    expect(authRedirect("/admin", admin)).toBeNull();
  });
});

describe("navigation", () => {
  it("shows Admin only to admins", () => {
    expect(navigationItems("member").some((item) => item.to === "/admin")).toBe(false);
    expect(navigationItems(null).some((item) => item.to === "/admin")).toBe(false);
    expect(navigationItems("admin").some((item) => item.to === "/admin")).toBe(true);
  });
});

describe("auth error fields", () => {
  it("places registration errors on the field they describe", () => {
    expect(fieldForAuthError("Use at least 8 characters.", "register")).toBe("password");
    expect(fieldForAuthError("Enter a valid email address.", "register")).toBe("email");
    expect(fieldForAuthError("Registration is limited to example.com addresses.", "register")).toBe(
      "email",
    );
    expect(fieldForAuthError("An account with this email already exists.", "register")).toBe(
      "email",
    );
    expect(fieldForAuthError("Email or password is incorrect.", "sign-in")).toBe("password");
    expect(fieldForAuthError("Unable to reach the server. Try again.", "sign-in")).toBe("form");
  });
});

describe("API proxy", () => {
  it("strips the /api prefix when forwarding to FastAPI", () => {
    expect(isApiPath("/api/auth/login")).toBe(true);
    expect(isApiPath("/search")).toBe(false);
    expect(apiUpstreamUrl("http://app.test/api/auth/me?x=1", "http://127.0.0.1:8000").href).toBe(
      "http://127.0.0.1:8000/auth/me?x=1",
    );
  });
});
