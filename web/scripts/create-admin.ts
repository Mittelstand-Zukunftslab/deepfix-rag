// Create an admin account, or promote an existing one. Registration never creates admins.
// Usage: bun run auth:create-admin --email admin@example.com   (password is read from stdin)
import { existsSync } from "node:fs";
import { createInterface } from "node:readline/promises";
import { parseArgs } from "node:util";

import { getAuth, isAdminRole, MIN_PASSWORD_LENGTH } from "../src/lib/auth.server";
import { serverConfig } from "../src/lib/server-config";

async function readPassword(): Promise<string> {
  if (!process.stdin.isTTY) {
    const chunks: Buffer[] = [];
    for await (const chunk of process.stdin) chunks.push(Buffer.from(chunk));
    return Buffer.concat(chunks).toString("utf-8").split(/\r?\n/)[0] ?? "";
  }
  const prompt = createInterface({ input: process.stdin, output: process.stderr });
  try {
    return await prompt.question("Password: ");
  } finally {
    prompt.close();
  }
}

async function main(): Promise<number> {
  for (const file of [".env.local", ".env"]) {
    if (existsSync(file)) process.loadEnvFile(file);
  }
  const { values } = parseArgs({ options: { email: { type: "string" } } });
  const email = values.email?.trim().toLowerCase();
  if (!email) {
    console.error("Pass --email <address>.");
    return 1;
  }

  const auth = await getAuth();
  const context = await auth.$context;
  const existing = await context.internalAdapter.findUserByEmail(email);
  if (existing) {
    const role = (existing.user as { role?: string | null }).role;
    if (!isAdminRole(role)) {
      await context.internalAdapter.updateUser(existing.user.id, { role: "admin" });
    }
    console.error(`${email} is an admin.`);
    return 0;
  }

  const password = await readPassword();
  if (password.length < MIN_PASSWORD_LENGTH) {
    console.error(`Use at least ${MIN_PASSWORD_LENGTH} characters.`);
    return 1;
  }
  await auth.api.createUser({
    body: { email, password, name: email.split("@")[0] ?? email, role: "admin" },
  });
  console.error(`Created admin ${email} in ${serverConfig.authDatabasePath}.`);
  return 0;
}

process.exitCode = await main();
