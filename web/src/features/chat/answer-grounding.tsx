import type { Grounding } from "@/client";

import { Badge } from "@/components/ui/badge";
import { GROUNDING_BADGE } from "@/features/chat/grounding-copy";

/** The one badge an answer card shows, chosen by the backend's grounding value. */
export function GroundingBadge({ grounding }: { grounding: Grounding }) {
  const badge = GROUNDING_BADGE[grounding];
  return (
    <Badge
      variant="outline"
      className={`text-[11px] font-medium ${badge.className}`}
      data-grounding={grounding}
    >
      {badge.label}
    </Badge>
  );
}
