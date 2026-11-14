import {
  getInteraction,
  submitFeedback,
  submitFeedbackComment,
  type CommentReason,
  type FeedbackCommentResponse,
  type FeedbackResponse,
  type InteractionResponse,
} from "@/client";
import { messageOf, unwrap } from "@/services/api-result";

export class FeedbackRequestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "FeedbackRequestError";
  }
}

async function call<T>(request: () => Promise<{ data?: T; error?: unknown }>): Promise<T> {
  try {
    return unwrap(await request());
  } catch (error) {
    throw new FeedbackRequestError(messageOf(error));
  }
}

export const feedbackService = {
  submit(input: {
    interactionId: string;
    target: "passage" | "answer";
    rank?: number | null;
    action: "up" | "down" | "clear";
  }): Promise<FeedbackResponse> {
    return call(() =>
      submitFeedback({
        body: {
          interaction_id: input.interactionId,
          target: input.target,
          rank: input.rank,
          action: input.action,
        },
      }),
    );
  },

  /** Append an optional comment to the current vote. Only admins can read it. */
  comment(input: {
    interactionId: string;
    target: "passage" | "answer";
    rank?: number | null;
    reason: CommentReason | null;
    text: string;
  }): Promise<FeedbackCommentResponse> {
    return call(() =>
      submitFeedbackComment({
        body: {
          interaction_id: input.interactionId,
          target: input.target,
          rank: input.rank,
          reason: input.reason,
          text: input.text,
        },
      }),
    );
  },

  read(interactionId: string): Promise<InteractionResponse> {
    return call(() => getInteraction({ path: { interaction_id: interactionId } }));
  },
};
