import { z } from "zod";

/**
 * Contracts for the admin Hermes chat.
 *
 * The generated client carries the request and response shapes for the JSON
 * routes; these schemas are what the UI validates a streamed frame against and
 * what it types a component from. `deepfixActionKind` is the allowlist the
 * backend enforces (CHAT-007), repeated here so a card the server would refuse
 * cannot be rendered from a stale frame.
 */

export const deepfixActionKind = z.enum(["start_optimization_run", "cancel_optimization_run"]);
export type DeepfixActionKind = z.infer<typeof deepfixActionKind>;

export const chatThread = z.object({
  thread_id: z.string(),
  title: z.string(),
  created_at: z.string(),
  updated_at: z.string(),
  last_turn_status: z.enum(["running", "completed", "failed", "cancelled"]).nullable().optional(),
});
export type ChatThread = z.infer<typeof chatThread>;

export const chatMessage = z.object({
  message_id: z.string(),
  turn_id: z.string().nullable().optional(),
  role: z.enum(["user", "assistant"]),
  seq: z.number(),
  content: z.string(),
  created_at: z.string(),
});
export type ChatMessage = z.infer<typeof chatMessage>;

export const chatTurn = z.object({
  turn_id: z.string(),
  status: z.enum(["running", "completed", "failed", "cancelled"]),
  retry_of: z.string().nullable().optional(),
  error: z.string().nullable().optional(),
  first_token_ms: z.number().nullable().optional(),
  started_at: z.string(),
  ended_at: z.string().nullable().optional(),
});
export type ChatTurn = z.infer<typeof chatTurn>;

export const chatAction = z.object({
  action_id: z.string(),
  turn_id: z.string(),
  kind: z.string(),
  payload: z.string(),
  status: z.enum(["proposed", "confirmed", "rejected", "expired", "failed"]),
  created_at: z.string(),
  expires_at: z.string(),
  decided_at: z.string().nullable().optional(),
  result: z.string().nullable().optional(),
});
export type ChatAction = z.infer<typeof chatAction>;

export const chatRun = z.object({
  run_id: z.string(),
  status: z.enum(["running", "skipped", "rejected", "promoted", "succeeded", "failed"]),
  trigger: z.enum(["cron", "manual"]),
  reason: z.string().nullable().optional(),
  started_at: z.string(),
  ended_at: z.string().nullable().optional(),
  primary_metric: z.string().nullable().optional(),
  dev_delta: z.record(z.string(), z.number()).nullable().optional(),
  holdout_delta: z.record(z.string(), z.number()).nullable().optional(),
  holdout_wins: z.number().nullable().optional(),
  holdout_losses: z.number().nullable().optional(),
  mlflow_run_id: z.string().nullable().optional(),
  mlflow_url: z.string().nullable().optional(),
  steps: z.array(z.string()),
});
export type ChatRun = z.infer<typeof chatRun>;

export const chatThreadDetail = z.object({
  thread: chatThread,
  messages: z.array(chatMessage),
  turns: z.array(chatTurn),
  actions: z.array(chatAction),
});
export type ChatThreadDetail = z.infer<typeof chatThreadDetail>;

/** One action card as the terminal `data-deepfix-turn` frame carries it. */
export const chatActionCard = z.object({
  action_id: z.string(),
  turn_id: z.string(),
  kind: deepfixActionKind,
  payload: z.object({ reason: z.string().optional(), run_id: z.string().optional() }),
  status: z.enum(["proposed", "confirmed", "rejected", "expired", "failed"]),
  created_at: z.string(),
  expires_at: z.string(),
});
export type ChatActionCard = z.infer<typeof chatActionCard>;

/** The `data-deepfix-turn` part: the turn id, its status, and its cards. */
export const chatTurnFrame = z.object({
  turn_id: z.string(),
  status: z.enum(["running", "completed", "failed", "cancelled"]),
  actions: z.array(chatActionCard),
});
export type ChatTurnFrame = z.infer<typeof chatTurnFrame>;

/** The `data-deepfix-tool` part: one Hermes tool-progress event. */
export const chatToolProgress = z.object({
  label: z.string().optional(),
  state: z.string().optional(),
  tool: z.string().optional(),
  preview: z.string().optional(),
});
export type ChatToolProgress = z.infer<typeof chatToolProgress>;

export const chatErrorCode = z.enum(["chat_disabled", "chat_misconfigured"]);
export type ChatErrorCode = z.infer<typeof chatErrorCode>;
