// Server-only environment. Browser-safe settings live in ./config.ts.
import { homedir } from "node:os";
import { join } from "node:path";

function env(name: string): string | undefined {
  const value = process.env[name]?.trim();
  return value ? value : undefined;
}

export function internalApiBase(): string {
  return (env("API_INTERNAL_URL") ?? "http://127.0.0.1:8000").replace(/\/$/, "");
}

export const serverConfig = {
  get authBaseUrl(): string {
    return (env("BETTER_AUTH_URL") ?? "http://localhost:8080").replace(/\/$/, "");
  },
  get authSecret(): string | undefined {
    return env("BETTER_AUTH_SECRET");
  },
  get authDatabasePath(): string {
    return env("AUTH_DATABASE_PATH") ?? join(homedir(), ".deepfix", "web", "auth.db");
  },
  get allowedEmailDomains(): string[] {
    return (env("ALLOWED_EMAIL_DOMAINS") ?? "")
      .split(",")
      .map((domain) => domain.trim().toLowerCase())
      .filter(Boolean);
  },
};
