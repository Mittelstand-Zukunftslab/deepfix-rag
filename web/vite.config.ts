// @lovable.dev/vite-tanstack-config already includes the following — do NOT add them manually
// or the app will break with duplicate plugins:
//   - TanStack devtools (dev-only, first), tanstackStart, viteReact, tailwindcss, tsConfigPaths,
//     nitro (build-only using cloudflare as a default target), VITE_* env injection, @ path alias,
//     React/TanStack dedupe, error logger plugins, and sandbox detection (port/host/strictPort).
// You can pass additional config via defineConfig({ vite: { ... }, etc... }) if needed.
import { existsSync } from "node:fs";

import { defineConfig } from "@lovable.dev/vite-tanstack-config";

// Server code (Better Auth, the /api proxy) reads process.env. Vite only exposes VITE_* to the
// client, so load the env files here for `vite dev`. The shell wins, then .env.local, then .env.
for (const file of [".env.local", ".env"]) {
  if (existsSync(file)) process.loadEnvFile(file);
}

export default defineConfig({
  tanstackStart: {
    // Redirect TanStack Start's bundled server entry to src/server.ts (our SSR error wrapper,
    // Better Auth's /api/auth routes, and the authenticated /api proxy to FastAPI).
    // nitro/vite builds from this
    server: { entry: "server" },
  },
  // The app runs as a local Node server with a SQLite auth database, not on Cloudflare.
  nitro: { preset: "node-server" },
});
