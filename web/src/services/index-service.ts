import { getIndexStatus, rebuildIndex, type IndexStatusResponse } from "@/client";

export type IndexStatus = IndexStatusResponse;

export class IndexRequestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "IndexRequestError";
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

export const INDEX_PREPARING = "The index is being prepared. Try again in a minute.";

export const indexService = {
  async status(): Promise<IndexStatus> {
    try {
      const result = await getIndexStatus();
      if (result.error || !result.data) throw new IndexRequestError(errorDetail(result.error));
      return result.data;
    } catch (error) {
      if (error instanceof IndexRequestError) throw error;
      throw new IndexRequestError("Unable to reach the server. Try again.");
    }
  },

  async rebuild(): Promise<IndexStatus> {
    try {
      const result = await rebuildIndex();
      if (result.error || !result.data) throw new IndexRequestError(errorDetail(result.error));
      return result.data;
    } catch (error) {
      if (error instanceof IndexRequestError) throw error;
      throw new IndexRequestError("Unable to reach the server. Try again.");
    }
  },
};
