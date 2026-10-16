import { getRequestHeaders } from "@tanstack/react-start/server";

import { identityToken } from "./lib/auth.server";

/** Bearer header for the signed-in user of the current document request, if any. */
export async function incomingAuthorization(): Promise<string | undefined> {
  let headers: Headers;
  try {
    headers = new Headers(getRequestHeaders() as HeadersInit);
  } catch {
    return undefined;
  }
  const token = await identityToken(headers);
  return token ? `Bearer ${token}` : undefined;
}
