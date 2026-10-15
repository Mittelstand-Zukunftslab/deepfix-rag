import { retrieve, type RetrievalResponse } from "@/client";

export type SearchHit = RetrievalResponse["hits"][number];
export type SearchResponse = RetrievalResponse;

export class SearchRequestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "SearchRequestError";
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

export const searchService = {
  async search(query: string): Promise<SearchResponse> {
    try {
      const result = await retrieve({ body: { query } });
      if (result.error || !result.data) throw new SearchRequestError(errorDetail(result.error));
      return result.data;
    } catch (error) {
      if (error instanceof SearchRequestError) throw error;
      throw new SearchRequestError("Unable to reach the server. Try again.");
    }
  },
};
