# 04 — Accounts and roles

**What to build:** A member can register with email and password, sign in, and sign out. Uploads and votes are attributed to that account. Admins are created only from a configured bootstrap email or by another admin, and the app shows an Admin link only to admins. This ticket also introduces the product database, which lives in an API-private directory outside the agent workspace and is the source of truth for users and sessions.

**Blocked by:**

- 03 — Walking skeleton: FastAPI shell, generated client, and TanStack Start app

**Status:** ready-for-agent

- [ ] Register accepts email and password (minimum 8 characters) and creates a user with role member.
- [ ] If the registering email equals the configured bootstrap admin email, the user receives role admin. There is no "first user becomes admin" rule.
- [ ] Login sets an httpOnly, SameSite=Lax session cookie (Secure when served over HTTPS). Logout clears it. The current-user endpoint returns id, email, and role.
- [ ] Passwords are stored only as Argon2 hashes. No endpoint returns the hash.
- [ ] Sessions expire 14 days after creation.
- [ ] An admin can change a user's role to admin or member. Members receive 403. Demoting the last remaining admin returns 409.
- [ ] Sign-in, sign-out, and register screens exist. Errors appear under the field they belong to. After sign-in, the header shows the user's email and shows an Admin link only when the role is admin.
- [ ] Typecheck passes.
- [ ] Verify in the browser.
