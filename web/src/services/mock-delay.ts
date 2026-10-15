import { config } from "@/lib/config";

// Simulates network latency for mock services. Remove once real APIs are wired.
export const mockDelay = (ms: number = config.mockLatencyMs): Promise<void> =>
  new Promise((resolve) => setTimeout(resolve, ms));
