import type { FeedbackTarget } from "@/schemas/feedback";
import { feedbackService } from "@/services/feedback-service";

import { FeedbackComment } from "./feedback-comment";
import { commentKey, type useFeedbackComments } from "./use-feedback-comments";
import type { Vote } from "./vote-actions";

/** The comment form for one stored vote, sent through the feedback service. */
export function VoteComment({
  comments,
  interactionId,
  target,
  rank,
  vote,
}: {
  comments: ReturnType<typeof useFeedbackComments>;
  interactionId: string;
  target: FeedbackTarget;
  rank?: number;
  vote: Vote;
}) {
  const key = commentKey(interactionId, target, rank);
  return (
    <FeedbackComment
      target={target}
      vote={vote}
      slot={comments.slot(key)}
      onOpen={() => comments.open(key)}
      onChange={(draft) => comments.change(key, draft)}
      onSkip={() => comments.skip(key)}
      onSend={async (draft) => {
        await feedbackService.comment({
          interactionId,
          target,
          rank: target === "passage" ? rank : null,
          reason: draft.reason,
          text: draft.text,
        });
        comments.markSent(key);
      }}
    />
  );
}
