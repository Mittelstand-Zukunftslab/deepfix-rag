import { createServerFn } from "@tanstack/react-start";
import { getRequestHeaders } from "@tanstack/react-start/server";

import { getAuth, toAccount } from "@/lib/auth.server";
import type { Account } from "@/schemas/auth";

/** The signed-in account for this request, or null. */
export const getCurrentAccount = createServerFn({ method: "GET" }).handler(
  async (): Promise<Account | null> => {
    const auth = await getAuth();
    const session = await auth.api.getSession({
      headers: new Headers(getRequestHeaders() as HeadersInit),
    });
    return session ? toAccount(session.user) : null;
  },
);
