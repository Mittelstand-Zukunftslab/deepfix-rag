import type { Grounding } from "@/client";

/**
 * What the answer card says about its own grounding.
 *
 * The badge is the claim a member reads first, so it follows the backend's
 * `grounding` field, and for a grounded answer, **why** it is grounded:
 *
 * - `grounded` with citations: **Grounded**. This answer pointed at its evidence.
 * - `grounded` with no citations, but the turn made no search either: **Reused
 *   earlier evidence** (`reused_evidence`). The answer reworked a passage already
 *   cited earlier in the thread — making it shorter, translating it — so it has no
 *   markers of its own and made no new claims. Calling that "Partly grounded"
 *   punished the model for doing the right thing.
 * - `grounded` with no citations after a search: **Partly grounded**
 *   (`partly_grounded`). The evidence was there and the answer pointed at none of
 *   it, which is the defensible defect D-10 describes.
 *
 * The two are told apart by `searched`, which the server already knows and the page
 * already holds from the terminal frame: a reuse turn ran no search, so it has no
 * passages. That is the whole discriminator, and it needs no new stored value.
 */

export type GroundingBadgeState =
  | "grounded"
  | "partly_grounded"
  | "reused_evidence"
  | "declined"
  | "no_relevant_sources"
  | "not_searched";

export const GROUNDING_BADGE: Record<GroundingBadgeState, { label: string; className: string }> = {
  grounded: {
    label: "Grounded",
    className: "border-primary/25 bg-primary/10 text-primary",
  },
  partly_grounded: {
    label: "Partly grounded",
    className: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400",
  },
  // The answer came from evidence already on screen. It is not a warning: nothing
  // is missing and nothing was ignored.
  reused_evidence: {
    label: "Reused earlier evidence",
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
  // The turn answered without looking anything up. It is not a failure and not a
  // refusal, so it must not borrow either label: "Not found" would claim a search
  // happened, and "Grounded" would be untrue.
  not_searched: {
    label: "Not searched",
    className: "border-border bg-muted text-muted-foreground",
  },
};

/**
 * The state to render.
 *
 * `searched` is whether this turn ran a search. Omitted, it is treated as `true`,
 * which is the pre-existing behaviour: a grounded answer with no citations after a
 * search is what "Partly grounded" was written for.
 */
export function badgeState(
  grounding: Grounding,
  citations: { marker: number; rank: number; doc_id: string }[],
  searched = true,
): GroundingBadgeState {
  if (grounding === "grounded" && citations.length === 0) {
    return searched ? "partly_grounded" : "reused_evidence";
  }
  return grounding;
}

/** The factual line beside the badge: what the answer was made from, or why it is empty. */
export function groundingNote(grounding: Grounding, sourceCount: number): string {
  if (grounding === "grounded") {
    return sourceCount === 1 ? "1 passage used" : `${sourceCount} passages used`;
  }
  if (grounding === "declined") {
    return "Answer declined";
  }
  if (grounding === "not_searched") {
    return "Answered without searching";
  }
  return "No matching passages";
}
