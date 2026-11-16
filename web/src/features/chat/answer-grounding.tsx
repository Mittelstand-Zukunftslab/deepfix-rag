import type { Grounding } from "@/client";

import { Badge } from "@/components/ui/badge";
import {
  badgeState,
  GROUNDING_BADGE,
  type GroundingBadgeState,
} from "@/features/chat/grounding-copy";

/**
 * The one badge an answer card shows.
 *
 * It is chosen by the backend's grounding value, and for a grounded answer by
 * whether it cited a passage **and** whether the turn searched at all: a reused
 * answer has no markers of its own and no search behind it, so it is not "partly
 * grounded" — it points at evidence that is already on screen. Both are rendered
 * states over one stored value (D-10), so the server keeps sending `grounded`.
 */
export function GroundingBadge({
  grounding,
  citations = [],
  searched = true,
}: {
  grounding: Grounding;
  citations?: { marker: number; rank: number; doc_id: string }[];
  /** Whether this turn ran a search. A reuse turn did not, so it has no passages. */
  searched?: boolean;
}) {
  const state: GroundingBadgeState = badgeState(grounding, citations, searched);
  const badge = GROUNDING_BADGE[state];
  return (
    <Badge
      variant="outline"
      className={`text-[11px] font-medium ${badge.className}`}
      data-grounding={state}
    >
      {badge.label}
    </Badge>
  );
}
