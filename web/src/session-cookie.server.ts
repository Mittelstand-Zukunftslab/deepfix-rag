import { getRequestHeader } from "@tanstack/react-start/server";

/** Cookie header from the incoming document request, if this call is inside one. */
export function incomingCookie(): string | undefined {
  try {
    return getRequestHeader("cookie");
  } catch {
    return undefined;
  }
}
