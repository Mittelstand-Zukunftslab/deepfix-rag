import { useState } from "react";
import { BookOpen, Bot, Check, Copy, User as UserIcon } from "lucide-react";

import { ChatMarkdown } from "@/components/chat/markdown";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { GroundingBadge } from "@/features/chat/answer-grounding";
import { groundingNote } from "@/features/chat/grounding-copy";
import { commentKey, useFeedbackComments } from "@/features/feedback/use-feedback-comments";
import { VoteButtons, type Vote } from "@/features/feedback/vote-buttons";
import { VoteComment } from "@/features/feedback/vote-comment";
import type { Grounding } from "@/client";
import { feedbackService } from "@/services/feedback-service";
import type { AskHit } from "@/services/ask-service";

/**
 * One single-turn result, rendered as the page used to render a conversation.
 *
 * This is the Search screen's hand-off view: it is given a question, an answer,
 * an error, and the passages, and it draws them with the grounding badge and the
 * votes. It does not send anything and it holds no history — the conversation
 * page is `chat-page.tsx` and it talks to the server — so the two cannot drift
 * into two different ideas of what an answer looks like.
 */

export function AskResultView({
  askedQuery,
  answer = null,
  error = null,
  hits = [],
  grounding = "grounded",
  interactionId = null,
}: {
  askedQuery: string;
  answer?: string | null;
  error?: string | null;
  hits?: AskHit[];
  grounding?: Grounding;
  interactionId?: string | null;
}) {
  const comments = useFeedbackComments();
  const [answerVote, setAnswerVote] = useState<Vote>(null);
  const [passageVotes, setPassageVotes] = useState<Record<number, Vote>>({});
  const [copied, setCopied] = useState(false);

  const chooseAnswer = async (action: "up" | "down" | "clear") => {
    if (!interactionId) {
      setAnswerVote(action === "clear" ? null : action);
      return;
    }
    const saved = await feedbackService.submit({
      interactionId,
      target: "answer",
      action,
    });
    setAnswerVote(saved.vote ?? null);
    comments.voted(commentKey(interactionId, "answer"), "answer", saved.vote ?? null);
  };

  const choosePassage = async (hit: AskHit, action: "up" | "down" | "clear") => {
    const vote: Vote = action === "clear" ? null : action;
    if (!interactionId) {
      setPassageVotes((current) => ({ ...current, [hit.rank]: vote }));
      return;
    }
    const saved = await feedbackService.submit({
      interactionId,
      target: "passage",
      rank: hit.rank,
      action,
    });
    setPassageVotes((current) => ({ ...current, [hit.rank]: saved.vote ?? null }));
    comments.voted(commentKey(interactionId, "passage", hit.rank), "passage", saved.vote ?? null);
  };

  const copy = (text: string) => {
    void navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-start justify-end gap-3.5">
        <div className="max-w-[85%] rounded-2xl rounded-tr-xs bg-primary px-4 py-3 text-body-md text-primary-foreground shadow-sm">
          <p className="whitespace-pre-wrap leading-relaxed">{askedQuery}</p>
        </div>
        <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/20 text-primary ring-1 ring-primary/30">
          <UserIcon className="size-4" aria-hidden />
        </div>
      </div>

      {error ? (
        <div className="flex items-start gap-3.5">
          <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-destructive/15 text-destructive ring-1 ring-destructive/30">
            <Bot className="size-4" aria-hidden />
          </div>
          <p role="alert" className="pt-1.5 text-body-md font-medium text-destructive">
            {error}
          </p>
        </div>
      ) : null}

      {answer ? (
        <div className="flex items-start gap-3.5">
          <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-xs ring-4 ring-primary/10">
            <Bot className="size-4" aria-hidden />
          </div>
          <Card className="flex flex-1 flex-col gap-4 rounded-2xl rounded-tl-xs border-border/80 bg-card p-5 shadow-soft">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/50 pb-3">
              <div className="flex items-center gap-2">
                <h2 className="text-label-lg font-semibold text-foreground">Answer</h2>
                <GroundingBadge grounding={grounding} />
              </div>
              <span className="text-label-sm text-muted-foreground">
                {groundingNote(grounding, hits.length)}
              </span>
            </div>
            <div className="text-body-md leading-relaxed text-foreground">
              <ChatMarkdown prose={false}>{answer}</ChatMarkdown>
            </div>
            <div className="flex items-center justify-between border-t border-border/40 pt-3">
              <div className="flex items-center gap-2">
                <VoteButtons
                  vote={answerVote}
                  upLabel="Mark answer as good"
                  downLabel="Mark answer as bad"
                  onAction={(action) => void chooseAnswer(action)}
                />
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => copy(answer)}
                  className="size-8 rounded-lg text-muted-foreground hover:text-foreground"
                  aria-label="Copy answer"
                >
                  {copied ? (
                    <Check className="size-3.5 text-emerald-500" aria-hidden />
                  ) : (
                    <Copy className="size-3.5" aria-hidden />
                  )}
                </Button>
              </div>
            </div>
            {interactionId ? (
              <VoteComment
                comments={comments}
                interactionId={interactionId}
                target="answer"
                vote={answerVote}
              />
            ) : null}
          </Card>
        </div>
      ) : null}

      {hits.length > 0 ? (
        <aside className="w-full">
          <section className="flex flex-col gap-3 rounded-2xl border border-border/80 bg-muted/20 p-4">
            <div className="flex items-center justify-between border-b border-border/60 pb-2.5">
              <div className="flex items-center gap-2">
                <BookOpen className="size-4 text-primary" aria-hidden />
                <h2 className="text-label-md font-semibold uppercase tracking-wider text-foreground">
                  Passages
                </h2>
              </div>
              <span className="rounded-full bg-background px-2.5 py-0.5 text-label-sm font-medium text-muted-foreground ring-1 ring-border">
                {hits.length} {hits.length === 1 ? "source" : "sources"}
              </span>
            </div>
            <ol className="flex flex-col gap-3">
              {hits.map((hit) => (
                <li key={`${hit.doc_id}-${hit.rank}`}>
                  <Card className="flex flex-col gap-2 rounded-xl border border-border bg-card p-4 shadow-soft">
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-1.5 overflow-hidden">
                        <span className="flex size-5 shrink-0 items-center justify-center rounded bg-primary/10 font-mono text-label-sm font-semibold text-primary">
                          #{hit.rank}
                        </span>
                        <span
                          className="truncate text-label-sm font-medium text-foreground"
                          title={hit.document_filename}
                        >
                          {hit.document_filename}
                        </span>
                      </div>
                      <span className="shrink-0 font-mono text-label-sm tabular-nums text-muted-foreground">
                        {hit.score.toFixed(3)}
                      </span>
                    </div>
                    <p className="line-clamp-6 text-label-sm leading-relaxed text-foreground/85 hover:line-clamp-none">
                      {hit.text}
                    </p>
                    <div className="border-t border-border/40 pt-2">
                      <VoteButtons
                        vote={passageVotes[hit.rank] ?? hit.vote ?? null}
                        upLabel="Mark passage as relevant"
                        downLabel="Mark passage as not relevant"
                        onAction={(action) => void choosePassage(hit, action)}
                      />
                      {interactionId ? (
                        <div className="mt-2">
                          <VoteComment
                            comments={comments}
                            interactionId={interactionId}
                            target="passage"
                            rank={hit.rank}
                            vote={passageVotes[hit.rank] ?? hit.vote ?? null}
                          />
                        </div>
                      ) : null}
                    </div>
                  </Card>
                </li>
              ))}
            </ol>
          </section>
        </aside>
      ) : null}

      {!answer && !error ? (
        <p role="status" className="text-body-md text-muted-foreground">
          No answer yet.
        </p>
      ) : null}
    </div>
  );
}
