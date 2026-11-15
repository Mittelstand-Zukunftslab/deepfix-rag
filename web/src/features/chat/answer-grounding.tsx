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
 * It is chosen by the backend's grounding value and, when that value is
 * `grounded`, by whether the answer cited a passage. `partly_grounded` is a
 * rendered state rather than a stored one (D-10): the server keeps three values.
 */
export function GroundingBadge({
  grounding,
  citations = [],
}: {
  grounding: Grounding;
  citations?: { marker: number; rank: number; doc_id: string }[];
}) {
  const state: GroundingBadgeState = badgeState(grounding, citations);
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
