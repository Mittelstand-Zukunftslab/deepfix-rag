import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { ChatActionCard } from "@/schemas/admin-chat";

/** What the card will do, in one sentence the admin can read before pressing Confirm. */
export function actionSentence(card: ChatActionCard): string {
  if (card.kind === "start_optimization_run") return "Start a run now";
  return `Cancel run ${card.payload.run_id ?? "the running run"}`;
}

/**
 * One proposed action.
 *
 * The assistant can only propose; Confirm is the write, and it is the admin's own
 * click that runs it (CHAT-007). The card sits in the conversation column with the
 * same radius and tokens as the composer and the messages, so it reads as part of
 * the thread rather than as a dialog over it.
 */
export function ActionCard({
  card,
  error,
  onConfirm,
  onDismiss,
}: {
  card: ChatActionCard;
  /** A failure from the last confirm attempt, shown on the card (CHAT-007). */
  error?: string | undefined;
  onConfirm: (card: ChatActionCard) => Promise<void>;
  onDismiss: (card: ChatActionCard) => Promise<void>;
}) {
  const decided = card.status !== "proposed";
  return (
    <section
      role="group"
      aria-label={actionSentence(card)}
      className={cn(
        "flex flex-col gap-3 rounded-2xl border border-border bg-card p-4 shadow-soft",
        "border-s-2 border-s-primary",
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-label-lg text-foreground">{actionSentence(card)}</h3>
        <Badge variant={decided ? "soft" : "outline"} className="text-label-sm uppercase">
          {card.status}
        </Badge>
      </div>
      {card.payload.reason ? (
        <p className="text-body-sm text-muted-foreground">{card.payload.reason}</p>
      ) : null}
      <p className="text-label-sm text-muted-foreground">
        {decided
          ? `This action is ${card.status}.`
          : "Confirming runs it; nothing happens until you do."}
      </p>
      {error ? (
        <p role="alert" className="text-label-sm text-destructive">
          {error}
        </p>
      ) : null}
      <div className="flex gap-2">
        <Button size="sm" disabled={decided} onClick={() => void onConfirm(card)}>
          Confirm
        </Button>
        <Button size="sm" variant="ghost" disabled={decided} onClick={() => void onDismiss(card)}>
          Dismiss
        </Button>
      </div>
    </section>
  );
}
