import type { CommentReason } from "@/client";

export type { CommentReason };
export type FeedbackTarget = "answer" | "passage";

export const COMMENT_MAX_LENGTH = 1000;

export const COMMENT_REASON_LABEL: Record<CommentReason, string> = {
  wrong: "Wrong",
  incomplete: "Incomplete",
  ignored_documents: "Didn't use my documents",
  not_relevant: "Not relevant",
  outdated: "Outdated",
  other: "Other",
};

/** Reasons offered after a thumbs-down. A thumbs-up takes text only. */
export const COMMENT_REASONS: Record<FeedbackTarget, CommentReason[]> = {
  answer: ["wrong", "incomplete", "ignored_documents", "other"],
  passage: ["not_relevant", "outdated", "other"],
};

export interface CommentDraft {
  reason: CommentReason | null;
  text: string;
}

export const EMPTY_DRAFT: CommentDraft = { reason: null, text: "" };

export function isDraftEmpty(draft: CommentDraft): boolean {
  return draft.reason === null && draft.text.trim() === "";
}
