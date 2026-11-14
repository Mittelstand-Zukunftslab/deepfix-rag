export { internalApiBase } from "./lib/server-config";

/** True when the browser is calling the same-origin API prefix. */
export function isApiPath(pathname: string): boolean {
  return pathname === "/api" || pathname.startsWith("/api/");
}

/** Better Auth's routes. These stay in the web server and never reach FastAPI. */
export function isAuthPath(pathname: string): boolean {
  return pathname === "/api/auth" || pathname.startsWith("/api/auth/");
}

/** Map a browser `/api/...` URL onto the internal FastAPI origin. */
export function apiUpstreamUrl(requestUrl: string, internalBase: string): URL {
  const incoming = new URL(requestUrl);
  const stripped = incoming.pathname.replace(/^\/api/, "") || "/";
  const base = internalBase.replace(/\/$/, "");
  return new URL(`${stripped}${incoming.search}`, `${base}/`);
}

/**
 * Headers for the FastAPI call: browser cookies never leave the web server, and the caller's
 * identity travels as a short-lived bearer token instead.
 */
export function upstreamHeaders(
  incoming: Headers,
  protocol: string,
  token: string | null,
): Headers {
  const headers = new Headers(incoming);
  headers.delete("host");
  headers.delete("cookie");
  headers.delete("authorization");
  if (!headers.has("x-forwarded-proto")) headers.set("x-forwarded-proto", protocol);
  if (token) headers.set("authorization", `Bearer ${token}`);
  return headers;
}
