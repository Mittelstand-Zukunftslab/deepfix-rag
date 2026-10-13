import {
  getInteraction,
  submitFeedback,
  type FeedbackResponse,
  type InteractionResponse,
} from "@/client";

export class FeedbackRequestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "FeedbackRequestError";
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

export const feedbackService = {
  async submit(input: {
    interactionId: string;
    target: "passage" | "answer";
    rank?: number | null;
    action: "up" | "down" | "clear";
  }): Promise<FeedbackResponse> {
    try {
      const result = await submitFeedback({
        body: {
          interaction_id: input.interactionId,
          target: input.target,
          rank: input.rank,
          action: input.action,
        },
      });
      if (result.error || !result.data) throw new FeedbackRequestError(errorDetail(result.error));
      return result.data;
    } catch (error) {
      if (error instanceof FeedbackRequestError) throw error;
      throw new FeedbackRequestError("Unable to reach the server. Try again.");
    }
  },

  async read(interactionId: string): Promise<InteractionResponse> {
    try {
      const result = await getInteraction({ path: { interaction_id: interactionId } });
      if (result.error || !result.data) throw new FeedbackRequestError(errorDetail(result.error));
      return result.data;
    } catch (error) {
      if (error instanceof FeedbackRequestError) throw error;
      throw new FeedbackRequestError("Unable to reach the server. Try again.");
    }
  },
};
