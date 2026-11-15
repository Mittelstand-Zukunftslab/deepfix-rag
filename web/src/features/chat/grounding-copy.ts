import type { Grounding } from "@/client";

/**
 * What the answer card says about its own grounding.
 *
 * The badge is the claim a member reads first, so it follows the backend's
 * `grounding` field rather than the presence of text: a model refusal is text,
 * but it is not grounded in the corpus.
 */
export const GROUNDING_BADGE: Record<Grounding, { label: string; className: string }> = {
  grounded: {
    label: "Grounded",
    className: "border-primary/25 bg-primary/10 text-primary",
  },
  declined: {
    label: "Not found in the documents",
    className: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400",
  },
  no_relevant_sources: {
    label: "Not found in the documents",
    className: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400",
  },
};

/** The factual line beside the badge: what the answer was made from, or why it is empty. */
export function groundingNote(grounding: Grounding, sourceCount: number): string {
  if (grounding === "grounded") {
    return sourceCount === 1 ? "1 passage used" : `${sourceCount} passages used`;
  }
  if (grounding === "declined") {
    return "Answer declined";
  }
  return "No matching passages";
}
