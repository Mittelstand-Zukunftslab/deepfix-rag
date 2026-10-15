# 04 — Accounts and roles

**What to build:** A member can register with email and password, sign in, and sign out. Uploads and votes are attributed to that account. Admins are created only by a server-side command or by another admin, and the app shows an Admin link only to admins. This ticket also introduces the product database, which lives in an API-private directory outside the agent workspace and is the source of truth for users and sessions, and moves browser traffic to a same-origin `/api` proxy so the session cookie works wherever the app is deployed.

**Reference:** tasks/prd-product-ui.md, `### US-006: Email and password accounts with roles`

**Blocked by:**

- 03 — Walking skeleton: FastAPI shell, generated client, and TanStack Start app

**Status:** ready-for-agent

- [ ] The browser reaches the API only under the web origin at `/api`. The Vite dev server proxies `/api/*` to FastAPI, and the TanStack Start server does the same in production. The browser base URL in `web/src/api.ts` becomes `/api`.
- [ ] Server-side rendering calls the API at an internal URL from the environment and forwards the incoming request's cookie header. That logic lives in one place in `web/src/api.ts`.
- [ ] Register accepts email and password (minimum 8 characters) and creates a user with role member. When an allowed-email-domains setting is configured, any other domain returns 400 with "Registration is limited to <domains> addresses."
- [ ] Registration never creates an admin. The first admin is created on the server with a management command (`python -m deepfix_rag.api.manage create-admin --email <email>`) that reads the password from standard input. The command is not part of the `deepfix` CLI.
- [ ] Login sets an httpOnly, SameSite=Lax session cookie (Secure when served over HTTPS). Logout clears it. The current-user endpoint returns id, email, and role.
- [ ] Passwords are stored only as Argon2 hashes. No endpoint returns the hash.
- [ ] Sessions expire 14 days after creation.
- [ ] An admin can change a user's role to admin or member. Members receive 403. Demoting the last remaining admin returns 409.
- [ ] Sign-in, sign-out, and register screens exist. Errors appear under the field they belong to. After sign-in, the header shows the user's email and shows an Admin link only when the role is admin. Signed-out visitors to any other page are sent to sign in.
- [ ] `src/services/*` call the generated SDK. Zod schemas are used for form validation only.
- [ ] Typecheck passes.
- [ ] Verify in the browser.
