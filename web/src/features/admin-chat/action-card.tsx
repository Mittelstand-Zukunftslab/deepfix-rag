import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { ChatActionCard } from "@/schemas/admin-chat";

/** What the card will do, in one sentence the admin can read before pressing Confirm. */
export function actionSentence(card: ChatActionCard): string {
  if (card.kind === "start_optimization_run") return "Start a run now";
  return `Cancel run ${card.payload.run_id ?? "the running run"}`;
}

/**
 * One proposed action. The assistant can only propose; Confirm is the write,
 * and it is the admin's own click that runs it (CHAT-007).
 */
export function ActionCard({
  card,
  onConfirm,
  onDismiss,
}: {
  card: ChatActionCard;
  onConfirm: (card: ChatActionCard) => Promise<void>;
  onDismiss: (card: ChatActionCard) => Promise<void>;
}) {
  const decided = card.status !== "proposed";
  return (
    <Card role="group" aria-label={actionSentence(card)}>
      <CardHeader>
        <CardTitle className="text-label-lg">{actionSentence(card)}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {card.payload.reason ? (
          <p className="text-body-sm text-muted-foreground">{card.payload.reason}</p>
        ) : null}
        <p className="text-label-sm text-muted-foreground">
          {decided
            ? `This action is ${card.status}.`
            : "Confirming runs it; nothing happens until you do."}
        </p>
        <div className="flex gap-2">
          <Button size="sm" disabled={decided} onClick={() => void onConfirm(card)}>
            Confirm
          </Button>
          <Button size="sm" variant="ghost" disabled={decided} onClick={() => void onDismiss(card)}>
            Dismiss
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
