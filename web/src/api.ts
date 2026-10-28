// Runtime configuration for the generated API client (see runtimeConfigPath in openapi-ts.config.ts).
// This is the only place the API origin is configured; the generated SDK in ./client reads it.
// The browser uses the same-origin `/api` prefix, and src/server.ts swaps its session cookie for
// a bearer token. Server rendering calls API_INTERNAL_URL directly and adds the same token for the
// document request's user through the custom fetch below.
import type { CreateClientConfig } from "@/client/client.gen";

const BROWSER_BASE_URL = "/api";

function serverBaseUrl(): string {
  const configured = process.env["API_INTERNAL_URL"] ?? "http://127.0.0.1:8000";
  return configured.replace(/\/$/, "");
}

export async function sessionFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  if (!import.meta.env.SSR) {
    return globalThis.fetch(input, init);
  }

  const headers = new Headers(init?.headers);
  if (input instanceof Request) {
    input.headers.forEach((value, key) => {
      if (!headers.has(key)) headers.set(key, value);
    });
  }
  if (!headers.has("authorization")) {
    const { incomingAuthorization } = await import("./request-identity.server");
    const authorization = await incomingAuthorization();
    if (authorization) {
      headers.set("authorization", authorization);
    }
  }
  return globalThis.fetch(input, { ...init, headers });
}

export const createClientConfig: CreateClientConfig = (override) => ({
  ...override,
  baseUrl: import.meta.env.SSR ? serverBaseUrl() : BROWSER_BASE_URL,
  credentials: "include",
  fetch: sessionFetch,
});
