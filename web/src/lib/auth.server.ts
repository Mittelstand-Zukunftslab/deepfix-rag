import { mkdirSync } from "node:fs";
import { dirname } from "node:path";

import { betterAuth, type BetterAuthOptions } from "better-auth";
import { APIError, createAuthMiddleware } from "better-auth/api";
import { getMigrations } from "better-auth/db/migration";
import { admin, jwt } from "better-auth/plugins";
import { adminAc, userAc } from "better-auth/plugins/admin/access";
import { tanstackStartCookies } from "better-auth/tanstack-start";
import Database from "better-sqlite3";

import { serverConfig } from "./server-config";
import type { Account } from "@/schemas/auth";

export const MIN_PASSWORD_LENGTH = 8;
const SESSION_SECONDS = 14 * 24 * 60 * 60;

export type AuthSetup = {
  database: Database.Database;
  baseURL: string;
  secret?: string;
  allowedEmailDomains: string[];
};

export function domainMessage(domains: string[]): string {
  return `Registration is limited to ${domains.join(", ")} addresses.`;
}

/** Better Auth stores multiple roles as a comma-separated string. */
export function isAdminRole(role: string | null | undefined): boolean {
  return (role ?? "").split(",").some((part) => part.trim() === "admin");
}

export function toAccount(user: { id: string; email: string; role?: string | null }): Account {
  return { id: user.id, email: user.email, role: isAdminRole(user.role) ? "admin" : "member" };
}

function authOptions({ database, baseURL, secret, allowedEmailDomains }: AuthSetup) {
  return {
    appName: "Papergnx.ai",
    baseURL,
    basePath: "/api/auth",
    secret,
    database,
    // autoSignIn stays on: turning it off makes duplicate sign-ups look successful.
    emailAndPassword: { enabled: true, minPasswordLength: MIN_PASSWORD_LENGTH },
    session: { expiresIn: SESSION_SECONDS },
    hooks: {
      before: createAuthMiddleware(async (ctx) => {
        if (ctx.path === "/sign-up/email" && allowedEmailDomains.length > 0) {
          const email = String(ctx.body?.email ?? "")
            .trim()
            .toLowerCase();
          const domain = email.split("@").pop() ?? "";
          if (!allowedEmailDomains.includes(domain)) {
            throw new APIError("BAD_REQUEST", { message: domainMessage(allowedEmailDomains) });
          }
        }
        const demotes =
          ctx.path === "/admin/set-role" &&
          ![ctx.body?.role].flat().some((role) => isAdminRole(String(role)));
        if (demotes || ctx.path === "/admin/remove-user") {
          const target = await ctx.context.internalAdapter.findUserById(String(ctx.body?.userId));
          if (!isAdminRole((target as { role?: string | null } | null)?.role)) return;
          const admins = await ctx.context.adapter.count({
            model: "user",
            where: [{ field: "role", value: "admin" }],
          });
          if (admins <= 1) {
            throw new APIError("CONFLICT", { message: "The workspace needs at least one admin." });
          }
        }
      }),
    },
    plugins: [
      admin({
        defaultRole: "member",
        adminRoles: ["admin"],
        roles: { admin: adminAc, member: userAc },
      }),
      // FastAPI verifies these tokens against /api/auth/jwks. Keep the claims in sync with
      // deepfix_rag.api.identity.
      jwt({
        disableSettingJwtHeader: true,
        jwks: { keyPairConfig: { alg: "EdDSA", crv: "Ed25519" } },
        jwt: {
          issuer: baseURL,
          audience: baseURL,
          expirationTime: "15m",
          definePayload: ({ user }) => ({
            id: user.id,
            email: user.email,
            role: (user as { role?: string | null }).role ?? "member",
          }),
        },
      }),
      tanstackStartCookies(),
    ],
  } satisfies BetterAuthOptions;
}

function createAuth(setup: AuthSetup) {
  return betterAuth(authOptions(setup));
}

export type Auth = ReturnType<typeof createAuth>;

/**
 * Create or update Better Auth's tables, then build the instance. Migrating first keeps Better
 * Auth's startup schema check from reporting tables that are about to be created.
 */
export async function createMigratedAuth(setup: AuthSetup): Promise<Auth> {
  const options = authOptions(setup);
  const { runMigrations } = await getMigrations(options);
  await runMigrations();
  return betterAuth(options);
}

function openDatabase(path: string): Database.Database {
  mkdirSync(dirname(path), { recursive: true });
  const database = new Database(path);
  database.pragma("journal_mode = WAL");
  return database;
}

let shared: Promise<Auth> | undefined;

/** The app's Better Auth instance, with its SQLite schema migrated. */
export function getAuth(): Promise<Auth> {
  shared ??= createMigratedAuth({
    database: openDatabase(serverConfig.authDatabasePath),
    baseURL: serverConfig.authBaseUrl,
    secret: serverConfig.authSecret,
    allowedEmailDomains: serverConfig.allowedEmailDomains,
  }).catch((error: unknown) => {
    shared = undefined;
    throw error;
  });
  return shared;
}

/** A short-lived token naming the signed-in user, or null when there is no session. */
export async function identityToken(headers: Headers): Promise<string | null> {
  if (!headers.get("cookie")) return null;
  const auth = await getAuth();
  try {
    const { token } = await auth.api.getToken({ headers });
    return token;
  } catch (error) {
    if (error instanceof APIError && error.statusCode === 401) return null;
    throw error;
  }
}
