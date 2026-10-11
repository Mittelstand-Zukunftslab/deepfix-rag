// Runtime configuration for the generated API client (see runtimeConfigPath in openapi-ts.config.ts).
// This is the only place the API origin is configured; the generated SDK in ./client reads it.
// The browser uses the same-origin `/api` prefix. Server rendering calls API_INTERNAL_URL and
// forwards the document request's Cookie header through the custom fetch below.
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
  if (!headers.has("cookie")) {
    const { incomingCookie } = await import("./session-cookie.server");
    const cookie = incomingCookie();
    if (cookie) {
      headers.set("cookie", cookie);
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
