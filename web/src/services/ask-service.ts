import { askQuestion, type AskResponse } from "@/client";

export type AskHit = AskResponse["hits"][number];

export class AskRequestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AskRequestError";
  }
}

function errorDetail(error: unknown): string {
  if (
    error &&
    typeof error === "object" &&
    "detail" in error &&
    typeof error.detail === "string" &&
    error.detail.length > 0
  ) {
    return error.detail;
  }
  return "Unable to reach the server. Try again.";
}

export const askService = {
  async ask(query: string): Promise<AskResponse> {
    try {
      const result = await askQuestion({ body: { query } });
      if (result.error || !result.data) throw new AskRequestError(errorDetail(result.error));
      return result.data;
    } catch (error) {
      if (error instanceof AskRequestError) throw error;
      throw new AskRequestError("Unable to reach the server. Try again.");
    }
  },
};
