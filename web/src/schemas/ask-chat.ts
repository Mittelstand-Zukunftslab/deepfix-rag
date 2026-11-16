import { z } from "zod";

/**
 * Contracts for the Ask chat.
 *
 * The generated client carries the request and response shapes for the JSON
 * routes; these schemas are what the UI validates a streamed frame or a stored
 * turn against before it renders it. They mirror `src/schemas/admin-chat.ts`,
 * which does the same job for the admin chat: one source of truth for the type
 * and the runtime check, so a frame that does not match its contract is caught
 * where it arrives rather than rendering as `undefined`.
 */

export const askGrounding = z.enum(["grounded", "declined", "no_relevant_sources", "not_searched"]);
export type AskGrounding = z.infer<typeof askGrounding>;

export const askTurnStatus = z.enum(["running", "completed", "failed", "cancelled"]);
export type AskTurnStatus = z.infer<typeof askTurnStatus>;

/** One `[n]` marker and the passage it resolved to. */
export const askCitation = z.object({
  marker: z.number(),
  rank: z.number(),
  doc_id: z.string(),
});
export type AskCitation = z.infer<typeof askCitation>;

export const askThread = z.object({
  thread_id: z.string(),
  title: z.string(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type AskThread = z.infer<typeof askThread>;

/** One passage in a `search_documents` tool output, and in a stored turn. */
export const askPassage = z.object({
  rank: z.number(),
  doc_id: z.string(),
  filename: z.string(),
  text: z.string(),
  score: z.number(),
  truncated: z.boolean(),
});
export type AskPassage = z.infer<typeof askPassage>;

/**
 * One stored turn. `question`, `answer` and `interaction_id` are null until the
 * turn's `interactions` row exists, which is what a turn still `running` looks
 * like; `grounding` is null for the same reason.
 */
export const askTurn = z.object({
  turn_id: z.string(),
  seq: z.number(),
  status: askTurnStatus,
  retry_of: z.string().nullable().optional(),
  error: z.string().nullable().optional(),
  grounding: askGrounding.nullable().optional(),
  citations: z.array(askCitation),
  passages: z.array(askPassage).optional(),
  retrieval_query: z.string().nullable().optional(),
  first_token_ms: z.number().nullable().optional(),
  started_at: z.string(),
  ended_at: z.string().nullable().optional(),
  interaction_id: z.string().nullable().optional(),
  question: z.string().nullable().optional(),
  answer: z.string().nullable().optional(),
});
export type AskTurn = z.infer<typeof askTurn>;

export const askThreadDetail = z.object({
  thread: askThread,
  turns: z.array(askTurn),
});
export type AskThreadDetail = z.infer<typeof askThreadDetail>;

/** The terminal `data-deepfix-turn` part: the turn id, its status, and its verdict. */
export const askTurnFrame = z.object({
  turn_id: z.string(),
  status: askTurnStatus,
  actions: z.array(z.unknown()).optional(),
  grounding: askGrounding.optional(),
  citations: z.array(askCitation).optional(),
});
export type AskTurnFrame = z.infer<typeof askTurnFrame>;

/** The `data-deepfix-grounding` part: the verdict, its citations, and the floor used. */
export const askGroundingFrame = z.object({
  grounding: askGrounding,
  citations: z.array(askCitation),
  max_score: z.number(),
  cutoff: z.number(),
});
export type AskGroundingFrame = z.infer<typeof askGroundingFrame>;

/** The tool's output object (D-2), which is also what the sources panel reads. */
export const askSearchOutput = z.object({
  query: z.string(),
  grounding: z.enum(["grounded", "no_relevant_sources"]),
  passages: z.array(askPassage),
  max_score: z.number(),
});
export type AskSearchOutput = z.infer<typeof askSearchOutput>;
