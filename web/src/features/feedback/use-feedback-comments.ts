import { useCallback, useState } from "react";

import {
  COMMENT_REASONS,
  EMPTY_DRAFT,
  type CommentDraft,
  type FeedbackTarget,
} from "@/schemas/feedback";

import type { Vote } from "./vote-actions";

export function commentKey(interactionId: string, target: FeedbackTarget, rank?: number): string {
  return target === "answer" ? `${interactionId}:answer` : `${interactionId}:passage:${rank}`;
}

export interface CommentSlot {
  open: boolean;
  sent: boolean;
  draft: CommentDraft;
}

interface CommentsState {
  openKey: string | null;
  drafts: Record<string, CommentDraft>;
  sent: Record<string, true>;
}

function without<T>(record: Record<string, T>, key: string): Record<string, T> {
  if (!(key in record)) return record;
  const { [key]: _removed, ...rest } = record;
  return rest;
}

/**
 * The comment forms on one page. A vote opens its form, and only one form is open at a
 * time; text typed into a form that gets closed by another vote is kept as a draft.
 */
export function useFeedbackComments() {
  const [state, setState] = useState<CommentsState>({ openKey: null, drafts: {}, sent: {} });

  const voted = useCallback((key: string, target: FeedbackTarget, vote: Vote) => {
    setState((current) => {
      const sent = without(current.sent, key);
      if (vote === null) {
        return { ...current, sent, openKey: current.openKey === key ? null : current.openKey };
      }
      const draft = current.drafts[key];
      const keepReason =
        draft?.reason != null && vote === "down" && COMMENT_REASONS[target].includes(draft.reason);
      const drafts = draft
        ? { ...current.drafts, [key]: { ...draft, reason: keepReason ? draft.reason : null } }
        : current.drafts;
      return { openKey: key, drafts, sent };
    });
  }, []);

  const open = useCallback((key: string) => {
    setState((current) => ({ ...current, openKey: key, sent: without(current.sent, key) }));
  }, []);

  const change = useCallback((key: string, draft: CommentDraft) => {
    setState((current) => ({ ...current, drafts: { ...current.drafts, [key]: draft } }));
  }, []);

  const skip = useCallback((key: string) => {
    setState((current) => ({
      ...current,
      openKey: current.openKey === key ? null : current.openKey,
      drafts: without(current.drafts, key),
    }));
  }, []);

  const markSent = useCallback((key: string) => {
    setState((current) => ({
      openKey: current.openKey === key ? null : current.openKey,
      drafts: without(current.drafts, key),
      sent: { ...current.sent, [key]: true },
    }));
  }, []);

  const slot = useCallback(
    (key: string): CommentSlot => ({
      open: state.openKey === key,
      sent: key in state.sent,
      draft: state.drafts[key] ?? EMPTY_DRAFT,
    }),
    [state],
  );

  return { slot, voted, open, change, skip, markSent };
}
