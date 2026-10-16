import { fireEvent, render, screen } from "@testing-library/react";
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { toNodeHandler } from "better-auth/node";
import Database from "better-sqlite3";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import type { ReactNode } from "react";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { RegisterPage } from "@/features/auth/register-page";
import { SignInPage } from "@/features/auth/sign-in-page";
import { setAuthOrigin } from "@/lib/auth-client";
import { createAuth, migrateAuth } from "@/lib/auth.server";

const renderPage = (ui: ReactNode) => {
  const root = createRootRoute({ component: () => ui });
  const signIn = createRoute({
    getParentRoute: () => root,
    path: "/sign-in",
    component: () => null,
  });
  const register = createRoute({
    getParentRoute: () => root,
    path: "/register",
    component: () => null,
  });
  const router = createRouter({
    routeTree: root.addChildren([signIn, register]),
    history: createMemoryHistory({ initialEntries: ["/sign-in"] }),
  });
  return render(<RouterProvider router={router} />);
};

const fill = async (email: string, password: string) => {
  fireEvent.change(await screen.findByLabelText("Email"), { target: { value: email } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
};

describe("Auth pages against a real Better Auth server", () => {
  let server: Server;

  beforeAll(async () => {
    server = createServer();
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    const origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
    const auth = createAuth({
      database: new Database(":memory:"),
      baseURL: origin,
      secret: "test-secret-that-is-at-least-32-characters-long",
      allowedEmailDomains: ["example.com"],
    });
    await migrateAuth(auth);
    server.on("request", toNodeHandler(auth.handler));
    await auth.api.signUpEmail({
      body: { email: "taken@example.com", password: "correct-horse", name: "taken" },
    });
    setAuthOrigin(origin);
  });

  afterAll(() => {
    server.close();
  });

  it("shows a sign-in failure under the password field", async () => {
    renderPage(<SignInPage />);
    await fill("taken@example.com", "wrong-password");
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    const message = await screen.findByText("Email or password is incorrect.");
    expect(message.getAttribute("role")).toBe("alert");
    expect(screen.getByLabelText("Password").getAttribute("aria-invalid")).toBe("true");
    expect(screen.getByRole("button", { name: "Sign in" }).textContent).toBe("Sign in");
  });

  it("shows a closed-domain registration error under the email field", async () => {
    renderPage(<RegisterPage />);
    await fill("a@other.test", "longenough");
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(
      await screen.findByText("Registration is limited to example.com addresses."),
    ).toBeTruthy();
    expect(screen.getByLabelText("Email").getAttribute("aria-invalid")).toBe("true");
    expect(screen.getByLabelText("Password").getAttribute("aria-invalid")).not.toBe("true");
  });

  it("shows a duplicate email under the email field", async () => {
    renderPage(<RegisterPage />);
    await fill("taken@example.com", "longenough");
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByText("An account with this email already exists.")).toBeTruthy();
    expect(screen.getByLabelText("Email").getAttribute("aria-invalid")).toBe("true");
  });

  it("keeps client validation on the empty fields", async () => {
    renderPage(<SignInPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Enter an email address.")).toBeTruthy();
    expect(screen.getByText("Use at least 8 characters.")).toBeTruthy();
  });
});
