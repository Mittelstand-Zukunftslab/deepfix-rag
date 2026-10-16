import { z } from "zod";

export const HISTORY_PAGE_SIZE = 25;

export const historyKindSchema = z.enum(["retrieval", "ask"]);
export const historyFeedbackSchema = z.enum(["all", "any", "negative", "commented", "none"]);

/** Filters for the admin history. They live in the URL so a filtered view can be shared. */
export const historyFiltersSchema = z.object({
  page: z.coerce.number().int().min(1).catch(1).default(1),
  kind: historyKindSchema.optional().catch(undefined),
  feedback: historyFeedbackSchema.catch("all").default("all"),
  q: z.string().trim().max(200).optional().catch(undefined),
});

export type HistoryFilters = z.infer<typeof historyFiltersSchema>;
export type HistoryKind = z.infer<typeof historyKindSchema>;
export type HistoryFeedback = z.infer<typeof historyFeedbackSchema>;

export const historySearchSchema = historyFiltersSchema.extend({
  id: z.string().min(1).max(64).optional().catch(undefined),
});

export type HistorySearch = z.infer<typeof historySearchSchema>;
