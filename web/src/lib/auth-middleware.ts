import { createMiddleware } from "@tanstack/react-start";
import { getRequestHeaders, setResponseStatus } from "@tanstack/react-start/server";

import { getAuth, identityToken, toAccount } from "./auth.server";
import type { AccountRole } from "@/schemas/auth";

export class AuthorizationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AuthorizationError";
  }
}

/** Resolve the Better Auth session for a server function, or fail with 401. */
export const requireSession = createMiddleware({ type: "function" }).server(async ({ next }) => {
  const headers = new Headers(getRequestHeaders() as HeadersInit);
  const auth = await getAuth();
  const session = await auth.api.getSession({ headers });
  if (!session) {
    setResponseStatus(401);
    throw new AuthorizationError("Sign in to continue.");
  }
  return next({ context: { account: toAccount(session.user), headers } });
});

/**
 * Allow the server function only for `role`, and hand it a token for FastAPI.
 * FastAPI repeats the admin check as a backstop.
 */
export function requireRole(role: AccountRole, message = "You don't have access to this.") {
  return createMiddleware({ type: "function" })
    .middleware([requireSession])
    .server(async ({ next, context }) => {
      if (role === "admin" && context.account.role !== "admin") {
        setResponseStatus(403);
        throw new AuthorizationError(message);
      }
      const token = await identityToken(context.headers);
      if (!token) {
        setResponseStatus(401);
        throw new AuthorizationError("Sign in to continue.");
      }
      return next({ context: { apiHeaders: { authorization: `Bearer ${token}` } } });
    });
}
