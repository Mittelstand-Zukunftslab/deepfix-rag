// Runtime configuration for the generated API client (see runtimeConfigPath in openapi-ts.config.ts).
// This is the only place the API origin is configured; the generated SDK in ./client reads it.
import type { CreateClientConfig } from "@/client/client.gen";
import { config } from "@/lib/config";

export const createClientConfig: CreateClientConfig = (override) => ({
  ...override,
  baseUrl: config.apiBaseUrl,
  credentials: "include",
});
