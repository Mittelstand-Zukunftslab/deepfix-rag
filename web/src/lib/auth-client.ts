import { adminClient } from "better-auth/client/plugins";
import { createAuthClient } from "better-auth/react";

const AUTH_BASE_PATH = "/api/auth";

function createClient(baseURL?: string) {
  return createAuthClient({ baseURL, basePath: AUTH_BASE_PATH, plugins: [adminClient()] });
}

let client = createClient();

export function authClient(): ReturnType<typeof createClient> {
  return client;
}

/** Point the browser auth client at another origin. Tests use this for a local server. */
export function setAuthOrigin(origin: string): void {
  client = createClient(origin);
}
