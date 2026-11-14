import { createServerFn } from "@tanstack/react-start";
import { z } from "zod";

import {
  listInteractionHistory,
  reviewInteraction,
  type HistoryPageResponse,
  type InteractionReviewResponse,
} from "@/client";
import { requireRole } from "@/lib/auth-middleware";
import { HISTORY_PAGE_SIZE, historyFiltersSchema } from "@/schemas/history";
import { unwrap } from "@/services/api-result";

const adminOnly = requireRole("admin", "Only admins can review history.");

export const fetchHistory = createServerFn({ method: "GET" })
  .middleware([adminOnly])
  .validator(historyFiltersSchema)
  .handler(async ({ context, data }): Promise<HistoryPageResponse> => {
    return unwrap(
      await listInteractionHistory({
        query: {
          limit: HISTORY_PAGE_SIZE,
          offset: (data.page - 1) * HISTORY_PAGE_SIZE,
          kind: data.kind,
          feedback: data.feedback,
          q: data.q || undefined,
        },
        headers: context.apiHeaders,
      }),
    );
  });

export const fetchInteractionReview = createServerFn({ method: "GET" })
  .middleware([adminOnly])
  .validator(z.object({ id: z.string().min(1).max(64) }))
  .handler(async ({ context, data }): Promise<InteractionReviewResponse> => {
    return unwrap(
      await reviewInteraction({
        path: { interaction_id: data.id },
        headers: context.apiHeaders,
      }),
    );
  });
