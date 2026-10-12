// Centralized access to environment configuration.
// When migrating to Next.js, swap `import.meta.env.VITE_*` for `process.env.NEXT_PUBLIC_*` here only.
export const config = {
  appName: "Papergnx.ai",
  appVersion: "v3.2",
  // Browser calls stay on this origin. The dev server and production server proxy `/api`.
  apiBaseUrl: "/api",
  mockLatencyMs: Number(import.meta.env["VITE_MOCK_LATENCY_MS"] ?? 250),
} as const;
