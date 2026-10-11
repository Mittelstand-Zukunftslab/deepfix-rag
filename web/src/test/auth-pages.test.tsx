import { fireEvent, render, screen } from "@testing-library/react";
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it } from "vitest";

import { client } from "@/client/client.gen";
import { RegisterPage } from "@/features/auth/register-page";
import { SignInPage } from "@/features/auth/sign-in-page";

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

describe("Auth pages", () => {
  let server: Server | undefined;

  const listen = (status: number, body: unknown) =>
    new Promise<void>((resolve) => {
      server = createServer((_req, res) => {
        res.writeHead(status, { "content-type": "application/json" });
        res.end(JSON.stringify(body));
      }).listen(0, "127.0.0.1", () => {
        client.setConfig({
          baseUrl: `http://127.0.0.1:${(server?.address() as AddressInfo).port}`,
        });
        resolve();
      });
    });

  afterEach(() => {
    server?.close();
    server = undefined;
  });

  it("shows a sign-in failure under the password field", async () => {
    await listen(401, { detail: "Email or password is incorrect." });
    renderPage(<SignInPage />);
    fireEvent.change(await screen.findByLabelText("Email"), { target: { value: "a@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "longenough" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    const message = await screen.findByText("Email or password is incorrect.");
    expect(message.getAttribute("role")).toBe("alert");
    expect(screen.getByLabelText("Password").getAttribute("aria-invalid")).toBe("true");
    expect(screen.getByRole("button", { name: "Sign in" }).textContent).toBe("Sign in");
  });

  it("shows a closed-domain registration error under the email field", async () => {
    await listen(400, { detail: "Registration is limited to example.com addresses." });
    renderPage(<RegisterPage />);
    fireEvent.change(await screen.findByLabelText("Email"), { target: { value: "a@other.test" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "longenough" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(
      await screen.findByText("Registration is limited to example.com addresses."),
    ).toBeTruthy();
    expect(screen.getByLabelText("Email").getAttribute("aria-invalid")).toBe("true");
    expect(screen.getByLabelText("Password").getAttribute("aria-invalid")).not.toBe("true");
  });

  it("keeps client validation on the empty fields", async () => {
    renderPage(<SignInPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Enter an email address.")).toBeTruthy();
    expect(screen.getByText("Use at least 8 characters.")).toBeTruthy();
  });
});
