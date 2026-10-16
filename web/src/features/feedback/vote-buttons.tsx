import { ThumbsDown, ThumbsUp } from "lucide-react";

import { Button } from "@/components/ui/button";

import { nextVoteAction, type Vote } from "./vote-actions";

export type { Vote };

export function VoteButtons({
  vote,
  upLabel,
  downLabel,
  onAction,
}: {
  vote: Vote;
  upLabel: string;
  downLabel: string;
  onAction: (action: "up" | "down" | "clear") => void;
}) {
  return (
    <div className="flex gap-2">
      <Button
        type="button"
        variant={vote === "up" ? "default" : "outline"}
        className="h-11 px-3"
        aria-pressed={vote === "up"}
        aria-label={upLabel}
        onClick={() => onAction(nextVoteAction(vote, "up"))}
      >
        <ThumbsUp className={vote === "up" ? "fill-current" : undefined} />
      </Button>
      <Button
        type="button"
        variant={vote === "down" ? "destructive" : "outline"}
        className="h-11 px-3"
        aria-pressed={vote === "down"}
        aria-label={downLabel}
        onClick={() => onAction(nextVoteAction(vote, "down"))}
      >
        <ThumbsDown className={vote === "down" ? "fill-current" : undefined} />
      </Button>
    </div>
  );
}
