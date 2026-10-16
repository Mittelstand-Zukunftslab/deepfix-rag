import type {
  HistoryEntryResponse,
  HistoryPageResponse,
  InteractionReviewResponse,
} from "@/client";
import type { HistoryFilters } from "@/schemas/history";
import { messageOf } from "@/services/api-result";
import { fetchHistory, fetchInteractionReview } from "@/services/history.functions";

export type HistoryPage = HistoryPageResponse;
export type HistoryEntry = HistoryEntryResponse;
export type InteractionReview = InteractionReviewResponse;

export class HistoryRequestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "HistoryRequestError";
  }
}

async function call<T>(request: () => Promise<T>): Promise<T> {
  try {
    return await request();
  } catch (error) {
    throw new HistoryRequestError(messageOf(error));
  }
}

/** Every user's searches and asks, readable only through admin-checked server functions. */
export const historyService = {
  list(filters: HistoryFilters): Promise<HistoryPage> {
    return call(() => fetchHistory({ data: filters }));
  },

  review(id: string): Promise<InteractionReview> {
    return call(() => fetchInteractionReview({ data: { id } }));
  },
};
