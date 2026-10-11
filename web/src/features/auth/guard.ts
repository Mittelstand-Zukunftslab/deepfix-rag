import type { Account } from "@/schemas/auth";

export const PUBLIC_PATHS = ["/sign-in", "/register"] as const;

export type PublicPath = (typeof PUBLIC_PATHS)[number];

export function isPublicPath(pathname: string): pathname is PublicPath {
  return (PUBLIC_PATHS as readonly string[]).includes(pathname);
}

/** Where to send the visitor, or null when the current page is allowed. */
export function authRedirect(
  pathname: string,
  user: Account | null,
): "/sign-in" | "/search" | null {
  const isPublic = isPublicPath(pathname);
  if (!user && !isPublic) return "/sign-in";
  if (user && isPublic) return "/search";
  return null;
}
