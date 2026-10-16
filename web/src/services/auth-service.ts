import { authClient } from "@/lib/auth-client";
import type { Account } from "@/schemas/auth";
import { getCurrentAccount } from "@/services/session.functions";

export type { Account, AccountRole } from "@/schemas/auth";

export type AuthField = "email" | "password" | "form";

const UNREACHABLE = "Unable to reach the server. Try again.";

export class AuthRequestError extends Error {
  readonly field: AuthField;

  constructor(message: string, field: AuthField) {
    super(message);
    this.name = "AuthRequestError";
    this.field = field;
  }
}

export function fieldForAuthError(message: string, intent: "sign-in" | "register"): AuthField {
  if (message === UNREACHABLE) return "form";
  if (intent === "sign-in") return "password";
  if (/password|character/i.test(message)) return "password";
  return "email";
}

const MESSAGES: Record<string, string> = {
  INVALID_EMAIL_OR_PASSWORD: "Email or password is incorrect.",
  INVALID_EMAIL: "Enter a valid email address.",
  PASSWORD_TOO_SHORT: "Use at least 8 characters.",
  USER_ALREADY_EXISTS: "An account with this email already exists.",
  USER_ALREADY_EXISTS_USE_ANOTHER_EMAIL: "An account with this email already exists.",
};

/** Better Auth error → the copy the forms show. Unknown codes keep the server's message. */
export function authErrorMessage(error: { code?: string; message?: string } | null): string {
  if (!error) return UNREACHABLE;
  const known = error.code ? MESSAGES[error.code] : undefined;
  return known ?? (error.message || UNREACHABLE);
}

async function submitCredentials(
  intent: "sign-in" | "register",
  values: { email: string; password: string },
): Promise<void> {
  let failure: { code?: string; message?: string } | null;
  try {
    const result =
      intent === "sign-in"
        ? await authClient().signIn.email(values)
        : await authClient().signUp.email({ ...values, name: values.email.split("@")[0] ?? "" });
    if (!result.error) return;
    failure = result.error;
  } catch {
    failure = null;
  }
  const message = authErrorMessage(failure);
  throw new AuthRequestError(message, fieldForAuthError(message, intent));
}

export const authService = {
  register(values: { email: string; password: string }): Promise<void> {
    return submitCredentials("register", values);
  },

  signIn(values: { email: string; password: string }): Promise<void> {
    return submitCredentials("sign-in", values);
  },

  async currentUser(): Promise<Account | null> {
    try {
      return await getCurrentAccount();
    } catch {
      return null;
    }
  },

  async signOut(): Promise<void> {
    const result = await authClient().signOut();
    if (result.error) {
      throw new AuthRequestError("Unable to sign out. Try again.", "form");
    }
  },
};
