/** True when the browser is calling the same-origin API prefix. */
export function isApiPath(pathname: string): boolean {
  return pathname === "/api" || pathname.startsWith("/api/");
}

/** Map a browser `/api/...` URL onto the internal FastAPI origin. */
export function apiUpstreamUrl(requestUrl: string, internalBase: string): URL {
  const incoming = new URL(requestUrl);
  const stripped = incoming.pathname.replace(/^\/api/, "") || "/";
  const base = internalBase.replace(/\/$/, "");
  return new URL(`${stripped}${incoming.search}`, `${base}/`);
}

export function internalApiBase(): string {
  return (process.env["API_INTERNAL_URL"] ?? "http://127.0.0.1:8000").replace(/\/$/, "");
}
