// @vitest-environment node
import Database from "better-sqlite3";
import { createLocalJWKSet, jwtVerify, type JSONWebKeySet } from "jose";
import { beforeEach, describe, expect, it } from "vitest";

import { isAuthPath, upstreamHeaders } from "@/api-proxy";
import { createAuth, migrateAuth, toAccount, type Auth } from "@/lib/auth.server";

const ORIGIN = "http://localhost:8080";
const PASSWORD = "correct-horse";

async function freshAuth(allowedEmailDomains: string[] = []): Promise<Auth> {
  const auth = createAuth({
    database: new Database(":memory:"),
    baseURL: ORIGIN,
    secret: "test-secret-that-is-at-least-32-characters-long",
    allowedEmailDomains,
  });
  await migrateAuth(auth);
  return auth;
}

async function signUp(auth: Auth, email: string): Promise<string> {
  const { user } = await auth.api.signUpEmail({
    body: { email, password: PASSWORD, name: email },
  });
  return user.id;
}

async function sessionHeaders(auth: Auth, email: string): Promise<Headers> {
  const { headers } = await auth.api.signInEmail({
    body: { email, password: PASSWORD },
    returnHeaders: true,
  });
  const cookie = headers
    .getSetCookie()
    .map((value) => value.split(";")[0])
    .join("; ");
  return new Headers({ cookie });
}

async function makeAdmin(auth: Auth, userId: string): Promise<void> {
  const context = await auth.$context;
  await context.internalAdapter.updateUser(userId, { role: "admin" });
}

describe("Better Auth setup", () => {
  let auth: Auth;

  beforeEach(async () => {
    auth = await freshAuth();
  });

  it("registers members only and refuses a requested role", async () => {
    const { user } = await auth.api.signUpEmail({
      body: { email: "ada@example.com", password: PASSWORD, name: "ada" },
    });
    expect(toAccount(user)).toMatchObject({ email: "ada@example.com", role: "member" });

    await expect(
      auth.api.signUpEmail({
        body: { email: "bea@example.com", password: PASSWORD, name: "bea", role: "admin" } as {
          email: string;
          password: string;
          name: string;
        },
      }),
    ).rejects.toThrow("role is not allowed to be set");
  });

  it("rejects short passwords and closed domains", async () => {
    await expect(
      auth.api.signUpEmail({ body: { email: "a@example.com", password: "short", name: "a" } }),
    ).rejects.toMatchObject({ body: { code: "PASSWORD_TOO_SHORT" } });

    const closed = await freshAuth(["example.com"]);
    await expect(
      closed.api.signUpEmail({ body: { email: "a@other.test", password: PASSWORD, name: "a" } }),
    ).rejects.toMatchObject({
      body: { message: "Registration is limited to example.com addresses." },
    });
    await expect(signUp(closed, "a@EXAMPLE.com")).resolves.toBeTruthy();
  });

  it("issues Ed25519 tokens with the claims FastAPI reads", async () => {
    const id = await signUp(auth, "ada@example.com");
    const { token } = await auth.api.getToken({
      headers: await sessionHeaders(auth, "ada@example.com"),
    });
    const jwks = (await (
      await auth.handler(new Request(`${ORIGIN}/api/auth/jwks`))
    ).json()) as JSONWebKeySet;

    const { payload, protectedHeader } = await jwtVerify(token, createLocalJWKSet(jwks), {
      issuer: ORIGIN,
      audience: ORIGIN,
    });
    expect(protectedHeader.alg).toBe("EdDSA");
    expect(payload).toMatchObject({ sub: id, email: "ada@example.com", role: "member" });
    expect((payload.exp ?? 0) - (payload.iat ?? 0)).toBe(15 * 60);
  });

  it("lets an admin promote a member but never demote the last admin", async () => {
    const adminId = await signUp(auth, "admin@example.com");
    const memberId = await signUp(auth, "ada@example.com");
    await makeAdmin(auth, adminId);
    const adminHeaders = await sessionHeaders(auth, "admin@example.com");

    await expect(
      auth.api.setRole({
        body: { userId: adminId, role: "member" },
        headers: adminHeaders,
      }),
    ).rejects.toMatchObject({ body: { message: "The workspace needs at least one admin." } });
    await expect(
      auth.api.removeUser({ body: { userId: adminId }, headers: adminHeaders }),
    ).rejects.toMatchObject({ body: { message: "The workspace needs at least one admin." } });

    await auth.api.setRole({ body: { userId: memberId, role: "admin" }, headers: adminHeaders });
    const demoted = await auth.api.setRole({
      body: { userId: adminId, role: "member" },
      headers: adminHeaders,
    });
    expect(toAccount(demoted.user).role).toBe("member");
  });

  it("refuses role changes from members", async () => {
    await signUp(auth, "ada@example.com");
    const memberId = await signUp(auth, "bea@example.com");
    await expect(
      auth.api.setRole({
        body: { userId: memberId, role: "admin" },
        headers: await sessionHeaders(auth, "ada@example.com"),
      }),
    ).rejects.toMatchObject({ statusCode: 403 });
  });
});

describe("API proxy identity", () => {
  it("keeps Better Auth routes in the web server", () => {
    expect(isAuthPath("/api/auth/sign-in/email")).toBe(true);
    expect(isAuthPath("/api/auth")).toBe(true);
    expect(isAuthPath("/api/authors")).toBe(false);
    expect(isAuthPath("/api/documents")).toBe(false);
  });

  it("swaps browser cookies for a bearer token", () => {
    const incoming = new Headers({
      cookie: "better-auth.session_token=secret",
      authorization: "Bearer forged",
      host: "app.test",
      "content-type": "application/json",
    });
    const signedIn = upstreamHeaders(incoming, "https", "signed.jwt");
    expect(signedIn.get("cookie")).toBeNull();
    expect(signedIn.get("host")).toBeNull();
    expect(signedIn.get("authorization")).toBe("Bearer signed.jwt");
    expect(signedIn.get("content-type")).toBe("application/json");
    expect(signedIn.get("x-forwarded-proto")).toBe("https");

    const signedOut = upstreamHeaders(incoming, "http", null);
    expect(signedOut.get("authorization")).toBeNull();
    expect(signedOut.get("cookie")).toBeNull();
  });
});
