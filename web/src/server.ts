import "./lib/error-capture";

import { consumeLastCapturedError } from "./lib/error-capture";
import { renderErrorPage } from "./lib/error-page";
import {
  apiUpstreamUrl,
  internalApiBase,
  isApiPath,
  isAuthPath,
  upstreamHeaders,
} from "./api-proxy";
import { getAuth, identityToken } from "./lib/auth.server";

type ServerEntry = {
  fetch: (request: Request, env: unknown, ctx: unknown) => Promise<Response> | Response;
};

let serverEntryPromise: Promise<ServerEntry> | undefined;

async function getServerEntry(): Promise<ServerEntry> {
  if (!serverEntryPromise) {
    serverEntryPromise = import("@tanstack/react-start/server-entry").then(
      (m) => (m.default ?? m) as ServerEntry,
    );
  }
  return serverEntryPromise;
}

// h3 swallows in-handler throws into a normal 500 Response with body
// {"unhandled":true,"message":"HTTPError"} — try/catch alone never fires for those.
async function normalizeCatastrophicSsrResponse(response: Response): Promise<Response> {
  if (response.status < 500) return response;
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) return response;

  const body = await response.clone().text();
  if (!isH3SwallowedErrorBody(body)) return response;

  console.error(consumeLastCapturedError() ?? new Error(`h3 swallowed SSR error: ${body}`));
  return new Response(renderErrorPage(), {
    status: 500,
    headers: { "content-type": "text/html; charset=utf-8" },
  });
}

function isH3SwallowedErrorBody(body: string): boolean {
  try {
    const payload = JSON.parse(body) as { unhandled?: unknown; message?: unknown };
    return payload.unhandled === true && payload.message === "HTTPError";
  } catch {
    return false;
  }
}

async function proxyApi(request: Request): Promise<Response> {
  const incoming = new URL(request.url);
  const target = apiUpstreamUrl(request.url, internalApiBase());
  const headers = upstreamHeaders(
    request.headers,
    incoming.protocol.replace(":", ""),
    await identityToken(request.headers),
  );

  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  const upstream = await fetch(target, {
    method: request.method,
    headers,
    body: hasBody ? request.body : undefined,
    redirect: "manual",
    duplex: hasBody ? "half" : undefined,
  } as RequestInit);

  const responseHeaders = new Headers(upstream.headers);
  const setCookies =
    typeof upstream.headers.getSetCookie === "function" ? upstream.headers.getSetCookie() : [];
  if (setCookies.length > 0) {
    responseHeaders.delete("set-cookie");
  }
  const response = new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
  for (const cookie of setCookies) {
    response.headers.append("set-cookie", cookie);
  }
  return response;
}

export default {
  async fetch(request: Request, env: unknown, ctx: unknown) {
    const url = new URL(request.url);
    if (isAuthPath(url.pathname)) {
      try {
        const auth = await getAuth();
        return await auth.handler(request);
      } catch (error) {
        console.error(error);
        return new Response(JSON.stringify({ message: "Sign-in is unavailable." }), {
          status: 500,
          headers: { "content-type": "application/json" },
        });
      }
    }
    if (isApiPath(url.pathname)) {
      try {
        return await proxyApi(request);
      } catch (error) {
        console.error(error);
        return new Response(JSON.stringify({ detail: "The API is unavailable." }), {
          status: 502,
          headers: { "content-type": "application/json" },
        });
      }
    }

    try {
      const handler = await getServerEntry();
      const response = await handler.fetch(request, env, ctx);
      return await normalizeCatastrophicSsrResponse(response);
    } catch (error) {
      console.error(error);
      return new Response(renderErrorPage(), {
        status: 500,
        headers: { "content-type": "text/html; charset=utf-8" },
      });
    }
  },
};
