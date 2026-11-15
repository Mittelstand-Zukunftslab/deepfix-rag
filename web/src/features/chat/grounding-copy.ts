import type { Grounding } from "@/client";

/**
 * What the answer card says about its own grounding.
 *
 * The badge is the claim a member reads first, so it follows the backend's
 * `grounding` field plus, for a grounded answer, whether it cited anything: an
 * answer that had the evidence and pointed at none of it is a defect worth
 * seeing, not a success to celebrate (D-10). That is the one state this module
 * adds on top of ASK-001's three, and it adds no fourth stored value — the
 * server still sends `grounded`, `declined`, or `no_relevant_sources`.
 */

export type GroundingBadgeState =
  "grounded" | "partly_grounded" | "declined" | "no_relevant_sources";

export const GROUNDING_BADGE: Record<GroundingBadgeState, { label: string; className: string }> = {
  grounded: {
    label: "Grounded",
    className: "border-primary/25 bg-primary/10 text-primary",
  },
  partly_grounded: {
    label: "Partly grounded",
    className: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400",
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

/**
 * The state to render: grounded only when the answer is grounded *and* it cited
 * at least one passage, which is D-10's whole rule.
 */
export function badgeState(
  grounding: Grounding,
  citations: { marker: number; rank: number; doc_id: string }[],
): GroundingBadgeState {
  if (grounding === "grounded" && citations.length === 0) return "partly_grounded";
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
  return "No matching passages";
}
