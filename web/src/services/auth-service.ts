import { login, logout, me, register } from "@/client";
import type { UserResponse } from "@/client";
import type { Account } from "@/schemas/auth";

export type { Account, AccountRole } from "@/schemas/auth";

export type AuthField = "email" | "password" | "form";

export class AuthRequestError extends Error {
  readonly field: AuthField;

  constructor(message: string, field: AuthField) {
    super(message);
    this.name = "AuthRequestError";
    this.field = field;
  }
}

export function fieldForAuthError(message: string, intent: "sign-in" | "register"): AuthField {
  if (message === "Unable to reach the server. Try again.") return "form";
  if (intent === "sign-in") return "password";
  if (/password|character/i.test(message)) return "password";
  return "email";
}

function errorDetail(error: unknown): string {
  if (
    error &&
    typeof error === "object" &&
    "detail" in error &&
    typeof error.detail === "string" &&
    error.detail.length > 0
  ) {
    return error.detail;
  }
  return "Unable to reach the server. Try again.";
}

function toAccount(user: UserResponse): Account {
  return { id: user.id, email: user.email, role: user.role };
}

async function submitCredentials(
  intent: "sign-in" | "register",
  values: { email: string; password: string },
): Promise<Account> {
  try {
    const result =
      intent === "sign-in" ? await login({ body: values }) : await register({ body: values });
    if (result.error || !result.data) {
      const message = errorDetail(result.error);
      throw new AuthRequestError(message, fieldForAuthError(message, intent));
    }
    return toAccount(result.data);
  } catch (error) {
    if (error instanceof AuthRequestError) throw error;
    throw new AuthRequestError("Unable to reach the server. Try again.", "form");
  }
}

export const authService = {
  register(values: { email: string; password: string }): Promise<Account> {
    return submitCredentials("register", values);
  },

  signIn(values: { email: string; password: string }): Promise<Account> {
    return submitCredentials("sign-in", values);
  },

  async currentUser(): Promise<Account | null> {
    try {
      const result = await me();
      if (!result.response?.ok || !result.data) return null;
      return toAccount(result.data);
    } catch {
      return null;
    }
  },

  async signOut(): Promise<void> {
    const result = await logout();
    if (!result.response?.ok) {
      throw new AuthRequestError("Unable to sign out. Try again.", "form");
    }
  },
};
